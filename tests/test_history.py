import unittest
from types import SimpleNamespace
import pandas as pd
from procurement.history import run_history, validate_period
from procurement.forecast import METHODS


def data_fixture():
    return SimpleNamespace(
        monthly=pd.DataFrame({'supplier':'S','sku':'one','month':pd.date_range('2024-01-01',periods=32,freq='MS'),'quantity':30.}),
        catalog=pd.DataFrame([{'supplier':'S','sku':'one','unit':'шт','name':'Test'}]),
        as_of=pd.Timestamp('2026-09-22'),fingerprint='test')


class HistoricalTests(unittest.TestCase):
    def test_constant_demand_has_zero_error_and_all_methods_same_cohort(self):
        report,rows=run_history(data_fixture(),'2025-01','2025-12')
        self.assertEqual(report['coverage']['evaluated'],12)
        self.assertEqual(len(rows),12 * len(METHODS))
        self.assertTrue(all(m['n']==12 and m['wape']==0 for m in report['metrics']))
        self.assertTrue(all(m['fill_rate']==1 for m in report['simulations']))
        self.assertFalse(report['historical_orders_evaluable'])

    def test_future_sales_do_not_change_earlier_forecasts(self):
        data=data_fixture()
        _,before=run_history(data,'2025-01','2025-03')
        data.monthly.loc[data.monthly.month>='2025-02-01','quantity']=100000
        _,after=run_history(data,'2025-01','2025-03')
        self.assertEqual([r for r in before if r['month']=='2025-01-01'],
                         [r for r in after if r['month']=='2025-01-01'])

    def test_unknown_actual_is_excluded_from_forecasts_and_simulation(self):
        data=data_fixture()
        data.monthly.loc[data.monthly.month=='2025-06-01','quantity']=float('nan')
        report,rows=run_history(data,'2025-01','2025-12')
        self.assertEqual(report['coverage']['unknown_actual'],1)
        self.assertEqual(len(rows),11 * len(METHODS))
        self.assertEqual(report['simulations'],[])

    def test_current_month_and_reversed_period_are_rejected(self):
        for start,end in [('2026-08','2026-09'),('2025-12','2025-01')]:
            with self.assertRaises(ValueError):validate_period(start,end,'2026-09-22')


if __name__=='__main__':unittest.main()
