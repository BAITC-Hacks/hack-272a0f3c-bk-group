import io
import unittest
import zipfile
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from procurement.data import Dataset, month_date
from procurement.engine import build_report, plan_inventory, validate_settings
from procurement.forecast import predict, backtest, future_daily, simulate_policies
from procurement.export import export_zip, safe_cell
from procurement.storage import Store
from app import encode, settings_for_dataset


class ForecastTests(unittest.TestCase):
    def test_future_observations_do_not_change_prediction(self):
        history=pd.Series(30.,index=pd.date_range('2024-01-01',periods=30,freq='MS'))
        target=pd.Timestamp('2025-05-01')
        before=predict(history,target)
        history.loc[history.index>=target]=1e9
        self.assertEqual(predict(history,target),before)

    def test_partial_current_month_excluded(self):
        history=pd.Series(31.,index=pd.date_range('2024-01-01',periods=21,freq='MS'))
        base=future_daily(history,'2025-09-22',10)
        history.loc['2025-09-01']=1e9
        pd.testing.assert_series_equal(future_daily(history,'2025-09-22',10),base)

    def test_backtest_excludes_unknown_actual_and_uses_only_past(self):
        history=pd.Series(10.,index=pd.date_range('2024-01-01',periods=24,freq='MS'))
        history.iloc[-1]=np.nan
        records=backtest(history)
        self.assertEqual(len(records),20)
        self.assertTrue(all(r['forecast']==10 for r in records))

    def test_late_receipt_does_not_hide_early_shortage(self):
        daily=pd.Series(10.,index=pd.date_range('2026-09-23',periods=10))
        result=plan_inventory(daily,0,[{'arrival':pd.Timestamp('2026-10-02'),'quantity':200}],0,3,10)
        self.assertTrue(result['early_shortage'])
        self.assertEqual(result['shortage_date'],'2026-09-23')
        self.assertGreater(result['recommended_quantity'],0)

    def test_planned_arrival_matches_order_date_plus_lead(self):
        daily=pd.Series(5.,index=pd.date_range('2026-09-23',periods=20))
        result=plan_inventory(daily,70,[],10,3,6)
        self.assertEqual(pd.Timestamp(result['arrival_date']),pd.Timestamp(result['order_date'])+pd.Timedelta(days=3))
        self.assertEqual(result['recommended_quantity']%6,0)

    def test_settings_reject_nan_and_false_confirmation_strings(self):
        with self.assertRaises(ValueError): validate_settings({'lead_days':float('nan')})
        with self.assertRaises(ValueError): validate_settings({'incoming_confirmed':'false'})

    def test_simulation_excludes_unknown_actuals_and_has_comparable_policies(self):
        history=pd.Series(30.,index=pd.date_range('2024-01-01',periods=24,freq='MS'))
        results=simulate_policies(history)
        self.assertEqual({r['method'] for r in results},{'minmax','mean3','seasonal_naive','adaptive','selected'})
        self.assertTrue(all(r['demand']==180 and r['lost']==0 for r in results))
        history.iloc[-1]=np.nan
        self.assertEqual(simulate_policies(history),[])


def fixture():
    months=pd.date_range('2024-01-01',periods=33,freq='MS')
    monthly=pd.DataFrame({'supplier':'S','sku':'sku','month':months,'quantity':30.,'source':'monthly'})
    catalog=pd.DataFrame([{'supplier':'S','sku':'sku','unit':'шт','name':'Товар','key':'S|sku|шт'}])
    sales=pd.DataFrame([{'supplier':'S','Код':'sku','Ед.':'шт','Количество':30,'date':pd.Timestamp('2026-08-10')}])
    snapshots=pd.DataFrame([{'supplier':'S','sku':'sku','on_hand':10.,'reserved':0.,'free_stock':10.,'snapshot_date':pd.Timestamp('2026-09-22'),'source':'snapshot'}])
    constraints=pd.DataFrame([{'supplier':'S','sku':'sku','value':6.,'field':'Кратность','source':'constraints'}])
    data=Dataset(sales,monthly,pd.DataFrame(),catalog,pd.DataFrame(),snapshots,constraints,[],'fixture',pd.Timestamp('2026-09-22'))
    lines=pd.DataFrame(columns=['supplier','sku','Ед.','pattern_class'])
    return data,lines


class EndToEndTests(unittest.TestCase):
    def test_new_dataset_revokes_old_confirmations_and_json_is_strict(self):
        class FakeStore:
            def get(self,key,default):
                return {'settings':{'parameters_confirmed':True,'blank_months_zero':True},'settings_dataset':'old'}.get(key,default)
        settings=settings_for_dataset(FakeStore(),'new')
        self.assertFalse(settings['parameters_confirmed'])
        self.assertFalse(settings['blank_months_zero'])
        self.assertEqual(encode({'x':float('nan'),'date':pd.NaT}),b'{"x": null, "date": null}')

    def test_missing_confirmation_blocks_export_order(self):
        data,lines=fixture()
        report=build_report(data,lines)
        row=report['rows'][0]
        self.assertFalse(row['ready'])
        self.assertIsNone(row['quantity'])
        with zipfile.ZipFile(io.BytesIO(export_zip(report))) as archive:
            supplier=archive.read('S - проект заказа.csv').decode('utf-8-sig')
            self.assertEqual(len(supplier.splitlines()),1)

    def test_complete_inputs_produce_rounded_order_and_source_trace(self):
        data,lines=fixture()
        settings={k:True for k in ('parameters_confirmed','constraints_confirmed','incoming_confirmed','reserve_policy_confirmed')}
        report=build_report(data,lines,settings)
        row=report['rows'][0]
        self.assertTrue(row['ready'],row['blocks'])
        self.assertGreater(row['quantity'],0)
        self.assertEqual(row['quantity']%6,0)
        self.assertEqual(row['sources']['snapshot'],'snapshot')

    def test_missing_stock_never_becomes_zero(self):
        data,lines=fixture()
        data.snapshots=pd.DataFrame()
        report=build_report(data,lines)
        self.assertIsNone(report['rows'][0]['free_stock'])
        self.assertIsNone(report['rows'][0]['calculation'])

    def test_missing_months_require_explicit_zero_assumption(self):
        data,lines=fixture()
        data.monthly.loc[data.monthly.month.eq('2026-08-01'),'quantity']=np.nan
        row=build_report(data,lines)['rows'][0]
        self.assertTrue(any('Пропуски' in b for b in row['blocks']))
        row=build_report(data,lines,{'blank_months_zero':True})['rows'][0]
        self.assertFalse(any('Пропуски' in b for b in row['blocks']))

    def test_override_is_bound_to_source_fingerprint(self):
        data,lines=fixture()
        overrides={'S|sku|шт':{'dataset':'other','on_hand':100000,'reserved':0,'snapshot_date':'2026-09-22','reason':'test'}}
        row=build_report(data,lines,overrides=overrides)['rows'][0]
        self.assertEqual(row['on_hand'],10)

    def test_source_stock_mismatch_blocks_until_explicit_override(self):
        data,lines=fixture()
        data.snapshots.loc[0,'free_stock']=999
        row=build_report(data,lines)['rows'][0]
        self.assertTrue(any('не равен' in b for b in row['blocks']))

    def test_journal_persists_and_csv_formula_is_literal(self):
        temp_root=Path(__file__).resolve().parents[1]/'data'/'test-temp'
        temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            path=Path(folder)/'test.sqlite'
            Store(path).put('settings',{'lead':30},'test')
            self.assertEqual(Store(path).get('settings',{}),{'lead':30})
            self.assertEqual(len(Store(path).history()),1)
        self.assertEqual(safe_cell('=1+1'),"'=1+1")
        self.assertEqual(month_date('сент. 2026'),pd.Timestamp('2026-09-01'))


if __name__=='__main__': unittest.main()
