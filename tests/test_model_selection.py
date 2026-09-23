"""Regression cases for chronological, evidence-gated model selection.

All observations are synthetic.  Missing months mean unknown demand, never zero.
"""
import unittest

import numpy as np
import pandas as pd

from procurement.forecast import future_daily, predict, select_method, selection_details


def seasonal_history():
    pattern = [100., 10., 40., 180., 15., 60., 150., 20., 80., 200., 30., 90.]
    return pd.Series(pattern * 4, index=pd.date_range('2021-01-01', periods=48, freq='MS'))


class ModelSelectionTests(unittest.TestCase):
    def test_one_real_seasonal_validation_month_is_not_enough(self):
        # Only January has a previous-year observation.  Older apparent
        # seasonal predictions would merely be the model's mean fallback.
        history = pd.Series(
            [22560., 1997., 15000., 18000., 16000., 14000., 19000.,
             17000., 16000., 20000., 18000., 19000., 22560.],
            index=pd.date_range('2024-01-01', periods=13, freq='MS'),
        )
        target = pd.Timestamp('2025-02-01')
        self.assertEqual(select_method(history, target), 'mean3')
        self.assertEqual(predict(history, target, 'selected'), predict(history, target, 'mean3'))

    def test_sustained_seasonal_evidence_can_select_seasonal_model(self):
        history = seasonal_history()
        target = pd.Timestamp('2025-01-01')
        self.assertEqual(select_method(history, target), 'seasonal_naive')
        self.assertEqual(predict(history, target, 'selected'), 100.)

    def test_flat_demand_keeps_simple_baseline(self):
        history = seasonal_history() * 0 + 50.
        self.assertEqual(select_method(history, pd.Timestamp('2025-01-01')), 'mean3')

    def test_future_values_do_not_change_selection_or_explanation(self):
        history = seasonal_history()
        target = pd.Timestamp('2025-01-01')
        future = pd.Series(1e9, index=pd.date_range(target, periods=12, freq='MS'))
        original = selection_details(history, target)
        extended = selection_details(pd.concat([history, future]), target)
        for field in ('method', 'reason', 'regime'):
            self.assertEqual(original[field], extended[field])
        self.assertEqual(predict(history, target, 'selected'),
                         predict(pd.concat([history, future]), target, 'selected'))

    def test_unknown_actual_months_are_not_validation_evidence(self):
        history = seasonal_history()
        history.loc['2024-05-01':'2024-12-01'] = np.nan
        target = pd.Timestamp('2025-01-01')
        self.assertEqual(select_method(history, target), 'mean3')
        # Omitting unknown rows altogether must not turn older folds into
        # recent evidence, or create fictitious zero-sales observations.
        self.assertEqual(select_method(history.dropna(), target), 'mean3')

    def test_missing_seasonal_anchors_do_not_count_fallback_predictions(self):
        history = seasonal_history()
        history.loc['2023-05-01':'2023-12-01'] = np.nan
        target = pd.Timestamp('2025-01-01')
        # All twelve validation actuals exist, but most seasonal inputs do not.
        self.assertEqual(select_method(history, target), 'mean3')

    def test_model_requires_its_inputs_for_the_requested_month(self):
        history = seasonal_history()
        history.loc['2024-01-01'] = np.nan
        target = pd.Timestamp('2025-01-01')
        # There is ample perfect seasonal evidence, but the target anchor is
        # unknown.  A renamed mean fallback is not a seasonal forecast.
        self.assertEqual(select_method(history, target), 'mean3')

    def test_old_folds_cannot_fill_recent_calendar_gaps(self):
        history = seasonal_history()
        recent = pd.Series(
            [100., 15., 80., 30., 90.],
            index=pd.to_datetime(['2025-01-01', '2025-05-01', '2025-09-01',
                                  '2025-11-01', '2025-12-01']),
        )
        history = pd.concat([history, recent])
        self.assertEqual(select_method(history, pd.Timestamp('2026-01-01')), 'mean3')

    def test_selection_exposes_a_nonempty_reason_and_demand_regime(self):
        details = selection_details(seasonal_history(), pd.Timestamp('2025-01-01'))
        self.assertEqual(details['method'], 'seasonal_naive')
        self.assertIsInstance(details['reason'], str)
        self.assertTrue(details['reason'].strip())
        self.assertIsInstance(details['regime'], str)
        self.assertTrue(details['regime'].strip())

    def test_adaptive_partial_input_predictions_are_not_validation_evidence(self):
        history = pd.Series(
            [100. * 1.02 ** i for i in range(48)],
            index=pd.date_range('2021-01-01', periods=48, freq='MS'),
        )
        target = pd.Timestamp('2025-01-01')
        self.assertEqual(select_method(history, target), 'adaptive')
        # Each validation month now lacks either its seasonal anchor or some
        # recent/prior-year inputs.  The requested target still has every input.
        history.loc['2023-02-01':'2023-09-01'] = np.nan
        details = selection_details(history, target)
        adaptive = next(row for row in details['candidates'] if row['method'] == 'adaptive')
        self.assertTrue(adaptive['target_inputs_available'])
        self.assertEqual(adaptive['folds'], 0)
        self.assertFalse(adaptive['eligible'])
        self.assertEqual(details['method'], 'mean3')
        # Numerical fallback predictions exist, but must not count as evidence.
        self.assertTrue(all(predict(history, month, 'adaptive') is not None
                            for month in pd.date_range('2024-01-01', periods=12, freq='MS')))

    def test_multi_month_horizon_keeps_actuals_frozen_and_rechecks_inputs(self):
        history = pd.Series(
            [100. * 1.02 ** i for i in range(48)],
            index=pd.date_range('2021-01-01', periods=48, freq='MS'),
        )
        self.assertEqual(select_method(history, pd.Timestamp('2025-01-01')), 'adaptive')
        self.assertEqual(select_method(history, pd.Timestamp('2025-02-01')), 'mean3')
        daily = future_daily(history, '2025-01-15', 75, 'selected')
        for date, value in daily.items():
            month = date.to_period('M').to_timestamp()
            expected = predict(history, month, 'selected') / date.days_in_month
            self.assertAlmostEqual(value, expected)
        # Partial current-month actuals and future actuals must not enter the
        # horizon, even when callers pass a series containing those months.
        future = pd.Series(1e9, index=pd.date_range('2025-01-01', periods=3, freq='MS'))
        pd.testing.assert_series_equal(
            daily, future_daily(pd.concat([history, future]), '2025-01-15', 75, 'selected'))


if __name__ == '__main__':
    unittest.main()
