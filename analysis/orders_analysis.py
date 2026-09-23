import json
from pathlib import Path

import numpy as np
import pandas as pd


DATA_PATH = Path(__file__).with_name("orders_cache.pkl")
PROFILE_KEYS = ["supplier", "sku", "Ед."]
MIN_HISTORY = 20
SIMILAR_QUANTITY_TOLERANCE = 0.25
MIN_REPEATED_LARGE_LINES = 3
MIN_REPEATED_DATES = 3
MIN_REPEATED_MONTHS = 2


def build_invoice_lines(data):
    valid = (
        data["Документ"].astype(str).str.startswith("Расходная накладная ")
        & data["date"].notna()
        & data["Количество"].gt(0)
        & data["Код"].notna()
        & data["Ед."].notna()
        & data["Ед."].ne('')
    )
    sales = data.loc[valid].copy()
    sales["year"] = sales["date"].dt.year
    sales["invoice"] = sales["Документ"].astype(str).str.strip()
    sales["sku"] = sales["Код"].astype(str).str.strip()

    lines = sales.groupby(
        PROFILE_KEYS + ["year", "invoice"], as_index=False
    ).agg(
        qty=("Количество", "sum"),
        date=("date", "min"),
        name=("Номенклатура", "first"),
    )

    stats = lines.groupby(PROFILE_KEYS)["qty"].agg(
        n="count",
        median="median",
        q1=lambda values: values.quantile(0.25),
        q3=lambda values: values.quantile(0.75),
    ).reset_index()
    stats["threshold"] = np.maximum(
        3 * stats["median"],
        stats["q3"] + 3 * (stats["q3"] - stats["q1"]),
    )
    lines = lines.merge(stats, on=PROFILE_KEYS, validate="many_to_one")
    lines["eligible"] = lines["n"].ge(MIN_HISTORY)
    lines["large"] = lines["eligible"] & lines["qty"].gt(lines["threshold"])
    return sales, lines


def classify_invoice_line_patterns(lines, batch_rules=None):
    """Classify invoice-SKU lines without inferring customer identity.

    Repetition means that similar large quantities for the same SKU and unit
    occur in separate invoices over time. It does not mean that one customer
    placed those invoices.
    """
    result = lines.copy()
    # Reclassification must start from statistics, not a prior packaging floor.
    result['statistical_threshold'] = result.get('statistical_threshold', result['threshold'])
    rules = batch_rules or {}
    floors = []
    for key in zip(result.supplier, result.sku, result['Ед.']):
        rule = rules.get(key, {})
        pack = rule.get('pack_multiple', 0) or 0
        minimum = rule.get('minimum_order_quantity', 0) or 0
        floors.append(max(pack, np.ceil(minimum / pack) * pack if pack else minimum))
    result['batch_floor'] = floors
    result['threshold'] = np.maximum(result.statistical_threshold, result.batch_floor)
    result['large'] = result['eligible'] & result.qty.gt(result.threshold)
    result["pattern_class"] = "ordinary_batch"
    result["similar_large_count"] = 0
    result["similar_large_distinct_dates"] = 0
    result["similar_large_distinct_months"] = 0
    result["demand_treatment"] = "include_in_regular_demand"

    for _, index in result.groupby(PROFILE_KEYS).groups.items():
        group = result.loc[index].copy()
        if not group.large.any():
            continue
        group_dates = pd.to_datetime(group["date"]).dt.normalize()
        group_months = pd.to_datetime(group["date"]).dt.to_period("M")

        for row_index, row in group[group.large].iterrows():
            lower = row["qty"] * (1 - SIMILAR_QUANTITY_TOLERANCE)
            upper = row["qty"] * (1 + SIMILAR_QUANTITY_TOLERANCE)
            similar_mask = group["qty"].between(lower, upper, inclusive="both")
            similar_count = int(similar_mask.sum())
            distinct_dates = int(group_dates[similar_mask].nunique())
            distinct_months = int(group_months[similar_mask].nunique())

            result.at[row_index, "similar_large_count"] = similar_count
            result.at[row_index, "similar_large_distinct_dates"] = distinct_dates
            result.at[row_index, "similar_large_distinct_months"] = distinct_months

            repeating = (
                similar_count >= MIN_REPEATED_LARGE_LINES
                and distinct_dates >= MIN_REPEATED_DATES
                and distinct_months >= MIN_REPEATED_MONTHS
            )
            if repeating:
                result.at[row_index, "pattern_class"] = (
                    "repeating_wholesale_batch"
                )
                result.at[row_index, "demand_treatment"] = (
                    "include_in_regular_demand"
                )
            else:
                result.at[row_index, "pattern_class"] = "candidate_one_off"
                result.at[row_index, "demand_treatment"] = "scenario_review"

    result["explanation"] = result.apply(explain_pattern, axis=1) if len(result) else pd.Series(dtype=str)
    return result


def explain_pattern(row):
    packaging = (f" Учтена упаковка и минимальная партия: {row['batch_floor']:g} ед."
                 if row.get('batch_floor', 0) > 0 else '')
    if not row["eligible"]:
        return (
            "Недостаточно истории: для SKU менее "
            f"{MIN_HISTORY} положительных строк накладных."
        )
    if row["pattern_class"] == "ordinary_batch":
        if row.get('batch_floor', 0) > row.get('statistical_threshold', row['threshold']) and row['qty'] <= row['batch_floor']:
            return 'Обычная партия: объём не превышает подтверждённую упаковку или минимальную партию.' + packaging
        return (
            "Обычная партия: объем не превышает устойчивый порог для этого "
            "SKU и единицы измерения."
        ) + packaging
    if row["pattern_class"] == "repeating_wholesale_batch":
        return (
            "Повторяющаяся оптовая партия: найдены похожие крупные объемы "
            f"в {int(row['similar_large_count'])} строках накладных, "
            f"на {int(row['similar_large_distinct_dates'])} датах и "
            f"в {int(row['similar_large_distinct_months'])} месяцах. "
            "Партия остается частью регулярного спроса."
        ) + packaging
    return (
        "Кандидат на разовый выброс: объем значительно выше типичного, но "
        "похожая крупная партия не повторялась достаточно регулярно. Решение "
        "проверяется сценарием и не исключается автоматически."
    ) + packaging


def summarize(group):
    # Invoice numbers can repeat in exports from different suppliers, so the
    # purchase key is the source supplier plus the invoice number.
    invoices = group.groupby(["supplier", "invoice"]).agg(
        has_repeating_wholesale=(
            "pattern_class",
            lambda values: values.eq("repeating_wholesale_batch").any(),
        ),
        has_candidate_one_off=(
            "pattern_class",
            lambda values: values.eq("candidate_one_off").any(),
        ),
        any_eligible=("eligible", "any"),
        all_eligible=("eligible", "all"),
    )
    invoice_class = np.select(
        [
            invoices["has_candidate_one_off"],
            invoices["has_repeating_wholesale"],
        ],
        ["contains_candidate_one_off", "contains_repeating_wholesale"],
        default="ordinary",
    )
    invoice_class_counts = pd.Series(invoice_class).value_counts().to_dict()
    line_class_counts = group["pattern_class"].value_counts().to_dict()

    return {
        "invoices": int(len(invoices)),
        "invoice_classes": {
            key: int(value) for key, value in invoice_class_counts.items()
        },
        "line_classes": {
            key: int(value) for key, value in line_class_counts.items()
        },
        "all_lines_assessable": int(invoices["all_eligible"].sum()),
        "no_assessable_lines": int((~invoices["any_eligible"]).sum()),
        "sku_invoice_lines": int(len(group)),
        "eligible_lines": int(group["eligible"].sum()),
        "unit_volumes": {
            unit: {
                "total": float(unit_group["qty"].sum()),
                "repeating_wholesale_volume": float(
                    unit_group.loc[
                        unit_group["pattern_class"].eq(
                            "repeating_wholesale_batch"
                        ),
                        "qty",
                    ].sum()
                ),
                "candidate_one_off_volume": float(
                    unit_group.loc[
                        unit_group["pattern_class"].eq("candidate_one_off"),
                        "qty",
                    ].sum()
                ),
            }
            for unit, unit_group in group.groupby("Ед.")
        },
    }


def main():
    data = pd.read_pickle(DATA_PATH)
    sales, lines = build_invoice_lines(data)
    lines = classify_invoice_line_patterns(lines)

    examples = lines[
        lines["pattern_class"].ne("ordinary_batch")
        & lines["Ед."].eq("шт")
    ].sort_values("qty", ascending=False).head(10)

    output = {
        "data_grain": "one invoice-SKU-unit line after aggregation",
        "customer_identity": {
            "available": False,
            "limitation": (
                "Invoice numbers identify purchases, not buyers. Different "
                "invoices are never linked to one customer."
            ),
        },
        "method": {
            "profile": "pooled 2025-2026 history per supplier, SKU and unit",
            "minimum_history": MIN_HISTORY,
            "large_threshold": "max(3*median, Q3+3*IQR)",
            "similar_quantity_tolerance": SIMILAR_QUANTITY_TOLERANCE,
            "repeating_wholesale_rule": (
                "at least 3 similar large invoice lines on 3 distinct dates "
                "and in at least 2 distinct months"
            ),
            "treatment": (
                "repeating wholesale batches remain in regular demand; "
                "candidate one-offs require scenario review and are not "
                "automatically excluded"
            ),
        },
        "raw_rows": int(len(data)),
        "included_positive_sales_rows": int(len(sales)),
        "total": summarize(lines),
        "by_year": {
            str(year): summarize(group)
            for year, group in lines.groupby("year")
        },
        "by_supplier": {
            str(supplier): summarize(group)
            for supplier, group in lines.groupby("supplier")
        },
        "examples": examples[
            [
                "supplier",
                "year",
                "invoice",
                "sku",
                "name",
                "Ед.",
                "qty",
                "median",
                "threshold",
                "pattern_class",
                "similar_large_count",
                "similar_large_distinct_dates",
                "similar_large_distinct_months",
                "demand_treatment",
                "explanation",
            ]
        ].to_dict("records"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
