"""Optional customer analysis for future files with a real customer_id.

This module is disabled for the supplied data. Invoice numbers identify
purchases and must never be used as synthetic customer identifiers.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd


DATA_PATH = Path(__file__).with_name("orders_cache.pkl")

CUSTOMER_COLUMN_ALIASES = (
    "customer_id",
    "Код контрагента",
    "ID покупателя",
)


def find_customer_column(columns):
    normalized = {str(column).strip().casefold(): column for column in columns}
    for alias in CUSTOMER_COLUMN_ALIASES:
        match = normalized.get(alias.casefold())
        if match is not None:
            return match
    return None


def coefficient_of_variation(values):
    values = pd.Series(values, dtype="float64").dropna()
    if len(values) < 2 or values.mean() == 0:
        return np.nan
    return float(values.std(ddof=1) / values.mean())


def classify_customer(order_count, interval_cv):
    if order_count <= 1:
        return "new"
    if order_count >= 4 and pd.notna(interval_cv) and interval_cv <= 0.75:
        return "regular"
    return "repeat"


def build_profiles(data, customer_column):
    sales = data[
        data["Документ"].astype(str).str.startswith("Расходная накладная ")
        & data["date"].notna()
        & data["Количество"].gt(0)
        & data[customer_column].notna()
    ].copy()

    sales["customer_id"] = sales[customer_column].astype(str).str.strip()
    sales["invoice_id"] = sales["Документ"].astype(str).str.strip()

    orders = (
        sales.groupby(["supplier", "customer_id", "invoice_id"], as_index=False)
        .agg(
            order_date=("date", "min"),
            line_count=("Код", "count"),
            total_quantity=("Количество", "sum"),
        )
        .sort_values(["supplier", "customer_id", "order_date"])
    )
    orders["days_since_previous"] = (
        orders.groupby(["supplier", "customer_id"])["order_date"]
        .diff()
        .dt.days
    )

    profile_rows = []
    for (supplier, customer_id), group in orders.groupby(
        ["supplier", "customer_id"], sort=False
    ):
        intervals = group["days_since_previous"].dropna()
        interval_cv = coefficient_of_variation(intervals)
        order_count = int(len(group))
        profile_rows.append(
            {
                "supplier": supplier,
                "customer_id": customer_id,
                "segment": classify_customer(order_count, interval_cv),
                "order_count": order_count,
                "first_order": group["order_date"].min(),
                "last_order": group["order_date"].max(),
                "median_interval_days": (
                    float(intervals.median()) if len(intervals) else None
                ),
                "interval_cv": round(interval_cv, 3)
                if pd.notna(interval_cv)
                else None,
                "median_order_quantity": float(group["total_quantity"].median()),
            }
        )

    return pd.DataFrame(profile_rows), orders


def main():
    data = pd.read_pickle(DATA_PATH)
    customer_column = find_customer_column(data.columns)

    if customer_column is None:
        result = {
            "status": "disabled_no_customer_id",
            "used_by_current_demo": False,
            "rows": int(len(data)),
            "available_columns": [str(column) for column in data.columns],
            "accepted_customer_columns": list(CUSTOMER_COLUMN_ALIASES),
            "limitation": (
                "Invoice numbers identify purchases, not buyers. They are not "
                "converted to customer IDs and different invoices are not linked."
            ),
            "optional_future_extension": (
                "Customer profiling can be enabled only if a future company "
                "export contains a real stable customer or counterparty ID."
            ),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    profiles, orders = build_profiles(data, customer_column)
    result = {
        "status": "ready",
        "used_by_current_demo": False,
        "customer_column": str(customer_column),
        "orders": int(len(orders)),
        "customers": int(len(profiles)),
        "segments": profiles["segment"].value_counts().to_dict(),
        "classification": {
            "new": "1 completed order",
            "repeat": "2+ orders without stable periodicity",
            "regular": "4+ orders and interval coefficient of variation <= 0.75",
        },
        "note": "Thresholds are MVP defaults and must be calibrated with a buyer.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
