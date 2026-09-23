"""Synthetic operational edge cases; none of these fixtures are customer data."""
import math
import random
import unittest

import numpy as np
import pandas as pd

from analysis.procurement_logic import (round_purchase_quantity, recommended_purchase_quantity,
    confirmed_incoming_before_horizon)
from procurement.engine import build_report, plan_inventory, prepare_lines
from procurement.forecast import backtest, future_daily, predict, safety_from_errors, selection_details, simulate_policies
from test_mvp import fixture


CONFIRMED = {key: True for key in ('parameters_confirmed', 'constraints_confirmed',
    'incoming_confirmed', 'reserve_policy_confirmed')}


class InventoryStressTests(unittest.TestCase):
    def daily(self, values):
        return pd.Series(values, index=pd.date_range('2026-09-23', periods=len(values)))

    def test_fractional_packs_ignore_machine_noise_but_round_real_excess(self):
        self.assertAlmostEqual(round_purchase_quantity(.1 + .2, .1), .3)
        self.assertAlmostEqual(round_purchase_quantity(.300000001, .1), .4)
        self.assertAlmostEqual(round_purchase_quantity(.001, .1, .1 + .2), .3)
        self.assertEqual(round_purchase_quantity(0, .1, 100), 0)

    def test_invalid_numeric_constraints_are_rejected_even_without_a_purchase(self):
        for value in (float('inf'), float('-inf'), float('nan')):
            for args in ((value, 1, 0), (0, value, 0), (0, 1, value)):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    round_purchase_quantity(*args)

    def test_nonfinite_inventory_cannot_turn_into_a_zero_aggregate_purchase(self):
        for field, value in (('on_hand', np.inf), ('reserved', np.nan), ('incoming', -10),
            ('safety_stock', np.inf), ('demand_during_lead_time', -1)):
            inputs = {'demand_during_lead_time': 100, 'safety_stock': 10, 'on_hand': 20,
                      'reserved': 0, 'incoming': 0}
            inputs[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                recommended_purchase_quantity(**inputs)

    def test_receipt_confirmation_strings_do_not_count_as_confirmed(self):
        shipment = {'confirmed': 'false', 'quantity': 100, 'arrival_date': '2026-09-25'}
        with self.assertRaises(ValueError):
            confirmed_incoming_before_horizon([shipment], '2026-10-01')

    def test_submillimetre_requirements_survive_chart_precision(self):
        result = plan_inventory(self.daily([.0004]), 0, [], 0, 1, .0001)
        self.assertAlmostEqual(result['raw_required'], .0004)
        self.assertAlmostEqual(result['recommended_quantity'], .0004)
        self.assertAlmostEqual(result['trajectory'][0]['with_order'], 0)

    def test_decimal_balance_does_not_create_false_stockout(self):
        result = plan_inventory(self.daily([.1, .1, .1]), .3, [], 0, 1, .1)
        self.assertIsNone(result['shortage_date'])
        self.assertEqual(result['recommended_quantity'], 0)

    def test_existing_reservation_deficit_is_not_hidden_by_tomorrow_receipt(self):
        result = plan_inventory(self.daily([1.] * 10), -5,
            [{'arrival': pd.Timestamp('2026-09-23'), 'quantity': 100}], 0, 3, 1)
        self.assertEqual(result['shortage_date'], '2026-09-22')
        self.assertTrue(result['early_shortage'])
        self.assertEqual(result['recommended_quantity'], 0)

    def test_receipts_use_calendar_day_and_exclude_beyond_horizon(self):
        result = plan_inventory(self.daily([10.] * 3), 0,
            [{'arrival': pd.Timestamp('2026-09-23 15:00'), 'quantity': 30},
             {'arrival': pd.Timestamp('2026-12-01'), 'quantity': 1000}], 0, 1, 1)
        self.assertEqual(result['incoming_in_horizon'], 30)
        self.assertEqual(result['recommended_quantity'], 0)
        self.assertIsNone(result['shortage_date'])

    def test_invalid_paths_and_receipts_fail_explicitly(self):
        cases = [self.daily([]), self.daily([np.nan]), self.daily([np.inf]), self.daily([-1]),
            pd.Series([1, 1], index=pd.to_datetime(['2026-09-23', '2026-09-25']))]
        for daily in cases:
            with self.subTest(daily=daily.to_list()), self.assertRaises(ValueError):
                plan_inventory(daily, 10, [], 0, 1, 1)
        for shipment in ({'arrival': pd.NaT, 'quantity': 5},
            {'arrival': pd.Timestamp('2026-09-24'), 'quantity': np.inf},
            {'arrival': pd.Timestamp('2026-09-24'), 'quantity': -5},
            {'arrival': pd.Timestamp('2026-09-22'), 'quantity': 5}):
            with self.subTest(shipment=shipment), self.assertRaises(ValueError):
                plan_inventory(self.daily([1.] * 5), 10, [shipment], 0, 1, 1)

    def test_300_randomized_plans_cover_every_date_after_the_new_receipt(self):
        rng = random.Random(20260923)
        for case in range(300):
            daily = self.daily([rng.randint(0, 1500) / 100 for _ in range(60)])
            free = rng.randint(-5000, 20000) / 100
            safety = rng.randint(0, 2000) / 100
            lead = rng.randint(1, 30)
            pack = rng.choice([.001, .01, .1, 1., 6., 25.])
            moq = rng.choice([0, 10, 100])
            shipments = [{'arrival': daily.index[rng.randrange(60)],
                'quantity': rng.randint(0, 20000) / 100} for _ in range(rng.randrange(6))]
            result = plan_inventory(daily, free, shipments, safety, lead, pack, moq)
            quantity = result['recommended_quantity']
            with self.subTest(case=case):
                self.assertTrue(math.isfinite(quantity))
                self.assertGreaterEqual(quantity + 1e-8, result['raw_required'])
                if quantity > 0:
                    self.assertGreaterEqual(quantity + 1e-8, moq)
                    self.assertAlmostEqual(quantity / pack, round(quantity / pack), places=6)
                    self.assertEqual(pd.Timestamp(result['arrival_date']),
                        pd.Timestamp(result['order_date']) + pd.Timedelta(days=lead))
                    for row in result['trajectory']:
                        if row['date'] >= result['arrival_date']:
                            self.assertGreaterEqual(row['with_order'] + 1e-8, safety)
                # More available stock must not cause a larger purchase.
                better = plan_inventory(daily, free + 10, shipments, safety, lead, pack, moq)
                self.assertLessEqual(better['recommended_quantity'], quantity + 1e-8)


class ForecastInputStressTests(unittest.TestCase):
    def test_empty_and_future_only_history_remain_unknown(self):
        for history in (pd.Series(dtype=float, index=pd.DatetimeIndex([])),
            pd.Series([1000.], index=pd.to_datetime(['2027-01-01']))):
            self.assertIsNone(predict(history, pd.Timestamp('2026-10-01')))
            self.assertTrue(future_daily(history, '2026-09-22', 30).isna().all())

    def test_nonfinite_observations_are_unknown_and_not_validation_actuals(self):
        history = pd.Series(10., index=pd.date_range('2024-01-01', periods=24, freq='MS'))
        history.iloc[-1] = np.inf
        without = history.replace(np.inf, np.nan)
        target = pd.Timestamp('2026-01-01')
        self.assertEqual(predict(history, target, 'selected'), predict(without, target, 'selected'))
        self.assertEqual(backtest(history), backtest(without))
        self.assertEqual(simulate_policies(history), [])

    def test_zero_negative_intermittent_and_large_finite_demand(self):
        for values in ([0.] * 36, [-5.] * 36, [0., 0., 300.] * 12, [1e12] * 36):
            history = pd.Series(values, index=pd.date_range('2023-01-01', periods=36, freq='MS'))
            forecast = future_daily(history, '2026-01-15', 150)
            self.assertTrue(np.isfinite(forecast).all())
            self.assertTrue(forecast.ge(0).all())
            self.assertTrue(selection_details(history, pd.Timestamp('2026-01-01'))['reason'])
            for result in simulate_policies(history):
                self.assertTrue(all(math.isfinite(result[key]) for key in ('demand', 'lost', 'mean_inventory', 'ordered')))

    def test_safety_buffer_excludes_invalid_errors_and_needs_three_known_errors(self):
        records = [{'method': 'selected', 'error': value} for value in (-2, -4, np.inf, np.nan)]
        self.assertIsNone(safety_from_errors(records, .9, 30))
        records.append({'method': 'selected', 'error': -6})
        self.assertTrue(math.isfinite(safety_from_errors(records, .9, 30)))


class ReportInputStressTests(unittest.TestCase):
    def test_nonfinite_stock_and_invalid_reserve_cannot_become_exportable(self):
        for field, value in (('on_hand', np.inf), ('reserved', np.nan), ('reserved', -10)):
            data, lines = fixture()
            data.snapshots.loc[0, field] = value
            row = build_report(data, lines, CONFIRMED)['rows'][0]
            self.assertFalse(row['ready'])
            self.assertIsNone(row['quantity'])

    def test_invalid_month_is_blocked_even_with_blank_means_zero_setting(self):
        data, lines = fixture()
        data.monthly.loc[data.monthly.month.eq('2026-08-01'), 'quantity'] = np.inf
        row = build_report(data, lines, {**CONFIRMED, 'blank_months_zero': True})['rows'][0]
        self.assertFalse(row['ready'])
        self.assertTrue(any('бесконечные' in value for value in row['blocks']))

    def test_invalid_receipt_quantity_blocks_the_product_without_crashing_report(self):
        for quantity in (np.inf, np.nan, -1):
            data, lines = fixture()
            data.shipments = pd.DataFrame([{'supplier': 'S', 'sku': 'sku',
                'arrival': pd.Timestamp('2026-10-01'), 'quantity': quantity}])
            row = build_report(data, lines, CONFIRMED)['rows'][0]
            self.assertFalse(row['ready'])
            self.assertTrue(any('количество в поставке' in value for value in row['blocks']))

    def test_future_only_months_do_not_crash_or_create_a_forecast(self):
        data, lines = fixture()
        data.monthly['month'] += pd.DateOffset(years=10)
        row = build_report(data, lines, CONFIRMED)['rows'][0]
        self.assertFalse(row['ready'])
        self.assertIsNone(row['forecast_horizon'])
        self.assertIsNone(row['calculation'])

    def test_copied_inbound_workbook_does_not_silently_double_available_supply(self):
        data, lines = fixture()
        shipment = {'supplier': 'S', 'sku': 'sku', 'arrival': pd.Timestamp('2026-10-01'),
            'quantity': 50, 'shipment': 'Поставка 123', 'snapshot_date': pd.Timestamp('2026-09-22')}
        data.shipments = pd.DataFrame([{**shipment, 'source': 'original.xlsx'},
                                     {**shipment, 'source': 'copy.xlsx'}])
        row = build_report(data, lines, CONFIRMED)['rows'][0]
        self.assertFalse(row['ready'])
        self.assertIsNone(row['quantity'])
        self.assertTrue(any('Повторяющиеся строки поставок' in value for value in row['blocks']))

    def test_reformatted_copy_of_invoice_rows_still_requires_reconciliation(self):
        data, lines = fixture()
        data.sales = pd.concat([data.sales.assign(source='original.xlsx'),
                               data.sales.assign(source='reformatted.xlsx')], ignore_index=True)
        report = build_report(data, lines, CONFIRMED)
        row = report['rows'][0]
        self.assertFalse(row['ready'])
        self.assertTrue(any('дубли продаж' in value for value in row['blocks']))
        self.assertEqual(report['summary']['duplicate_sales_rows'], 1)

    def test_high_volume_stock_mismatch_cannot_hide_in_relative_tolerance(self):
        data, lines = fixture()
        data.snapshots.loc[0, ['on_hand', 'reserved', 'free_stock']] = [1_000_000, 0, 1_000_001]
        row = build_report(data, lines, CONFIRMED)['rows'][0]
        self.assertFalse(row['ready'])
        self.assertTrue(any('не равен' in value for value in row['blocks']))

    def test_robust_scenario_requires_absolute_reconciliation_at_large_volumes(self):
        data, _ = fixture()
        quantities = [10] * 20 + [1_000_000]
        data.sales = pd.DataFrame([{'supplier': 'S', 'Код': 'sku', 'Ед.': 'шт',
            'Номенклатура': 'Синтетический товар', 'Документ': f'Расходная накладная TEST-{index}',
            'Количество': quantity, 'date': pd.Timestamp('2026-08-01') + pd.Timedelta(days=index)}
            for index, quantity in enumerate(quantities)])
        data.monthly.loc[data.monthly.month.eq('2026-08-01'), 'quantity'] = 1_000_199
        row = build_report(data, prepare_lines(data), {**CONFIRMED, 'scenario': 'robust'})['rows'][0]
        self.assertGreater(row['candidate_count'], 0)
        self.assertFalse(row['ready'])
        self.assertIsNone(row['robust_forecast'])
        self.assertTrue(any('сверки накладных' in value for value in row['blocks']))


if __name__ == '__main__':
    unittest.main()
