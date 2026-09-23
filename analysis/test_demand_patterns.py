import unittest

import pandas as pd

from orders_analysis import (
    build_invoice_lines,
    classify_invoice_line_patterns,
    summarize,
)
from procurement_logic import (
    confirmed_incoming_before_horizon,
    recommended_purchase_quantity,
    round_purchase_quantity,
)


class DemandPatternTests(unittest.TestCase):
    def make_data(self):
        rows = []
        invoice_number = 1

        for date in pd.date_range("2025-01-01", periods=20, freq="7D"):
            rows.append(
                {
                    "Документ": f"Расходная накладная {invoice_number}",
                    "date": date,
                    "Количество": 10,
                    "Код": "SKU-1",
                    "Ед.": "шт",
                    "Номенклатура": "Тестовый товар",
                    "supplier": "Supplier",
                }
            )
            invoice_number += 1

        for date, quantity in [
            ("2025-06-01", 100),
            ("2025-07-01", 105),
            ("2025-08-01", 95),
            ("2025-09-01", 500),
        ]:
            rows.append(
                {
                    "Документ": f"Расходная накладная {invoice_number}",
                    "date": pd.Timestamp(date),
                    "Количество": quantity,
                    "Код": "SKU-1",
                    "Ед.": "шт",
                    "Номенклатура": "Тестовый товар",
                    "supplier": "Supplier",
                }
            )
            invoice_number += 1

        return pd.DataFrame(rows)

    def test_patterns_use_invoices_without_customer_identity(self):
        data = self.make_data()
        _, lines = build_invoice_lines(data)
        classified = classify_invoice_line_patterns(lines)

        repeating = classified[
            classified["pattern_class"].eq("repeating_wholesale_batch")
        ]
        candidate = classified[
            classified["pattern_class"].eq("candidate_one_off")
        ]

        self.assertEqual(set(repeating["qty"]), {95, 100, 105})
        self.assertEqual(candidate["qty"].tolist(), [500])
        self.assertTrue(
            repeating["demand_treatment"]
            .eq("include_in_regular_demand")
            .all()
        )
        self.assertEqual(candidate.iloc[0]["demand_treatment"], "scenario_review")
        self.assertNotIn("customer_id", classified.columns)
        self.assertIn("регулярного спроса", repeating.iloc[0]["explanation"])

    def test_pack_and_minimum_order_rounding(self):
        self.assertEqual(round_purchase_quantity(73, pack_multiple=20), 80)
        self.assertEqual(
            round_purchase_quantity(
                73,
                pack_multiple=20,
                minimum_order_quantity=100,
            ),
            100,
        )
        self.assertEqual(round_purchase_quantity(0, pack_multiple=20), 0)

    def test_purchase_recommendation_uses_inventory_position(self):
        result = recommended_purchase_quantity(
            demand_during_lead_time=190,
            safety_stock=50,
            on_hand=80,
            reserved=20,
            incoming=40,
            pack_multiple=20,
            minimum_order_quantity=100,
        )
        self.assertEqual(result["inventory_position"], 100)
        self.assertEqual(result["raw_required"], 140)
        self.assertEqual(result["recommended_quantity"], 140)

    def test_invalid_pack_multiple_is_rejected(self):
        with self.assertRaises(ValueError):
            round_purchase_quantity(10, pack_multiple=0)

    def test_only_confirmed_incoming_before_horizon_is_counted(self):
        incoming = confirmed_incoming_before_horizon(
            [
                {
                    "arrival_date": "2026-10-01",
                    "quantity": 40,
                    "confirmed": True,
                },
                {
                    "arrival_date": "2026-10-20",
                    "quantity": 80,
                    "confirmed": True,
                },
                {
                    "arrival_date": "2026-09-28",
                    "quantity": 30,
                    "confirmed": False,
                },
            ],
            horizon_end="2026-10-10",
        )

        self.assertEqual(incoming, 40)

    def test_invoice_number_is_scoped_by_supplier(self):
        data = pd.concat(
            [
                self.make_data().assign(supplier="Supplier A"),
                self.make_data().assign(supplier="Supplier B"),
            ],
            ignore_index=True,
        )
        _, lines = build_invoice_lines(data)
        classified = classify_invoice_line_patterns(lines)

        self.assertEqual(summarize(classified)["invoices"], 48)


if __name__ == "__main__":
    unittest.main()
