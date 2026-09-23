"""Regression coverage for catalogue, batch analysis and cache dependencies."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import app
from analysis.orders_analysis import build_invoice_lines, classify_invoice_line_patterns
from procurement.data import build_catalog
from procurement.engine import build_report, prepare_lines
from test_mvp import fixture


def invoice_data(ordinary=30, batches=(80, 90, 100)):
    quantities = [ordinary] * 40 + list(batches)
    return pd.DataFrame([{'supplier': 'S', 'Код': 'sku', 'Ед.': 'шт',
        'Номенклатура': 'Товар', 'Документ': f'Расходная накладная {i}',
        'Количество': qty, 'date': pd.Timestamp('2024-01-01') + pd.DateOffset(months=i)}
        for i, qty in enumerate(quantities)])


class CatalogTests(unittest.TestCase):
    def test_union_keeps_products_from_every_source_with_provenance(self):
        sales = invoice_data().assign(source='invoices')
        sources = [pd.DataFrame([{'supplier': 'S', 'sku': label, 'unit': '',
            'name': label, 'source': label}])
            for label in ('monthly', 'balances', 'snapshots', 'constraints', 'shipments')]
        catalog = build_catalog(sales, *sources)
        self.assertEqual(set(catalog.sku), {'sku','monthly','balances','snapshots','constraints','shipments'})
        missing = catalog[catalog.sku.eq('shipments')].iloc[0]
        self.assertEqual(missing.unit, '')
        self.assertEqual(missing.catalog_sources, ['shipments'])

    def test_explicit_balance_unit_completes_missing_invoice_unit(self):
        sales = invoice_data().assign(**{'Ед.': ''})
        balances = pd.DataFrame([{'supplier':'S','sku':'sku','unit':'шт','name':'Товар','source':'balance'}])
        catalog = build_catalog(sales, balances)
        self.assertEqual(catalog.unit.tolist(), ['шт'])
        conflict = build_catalog(invoice_data(), balances.assign(unit='м'))
        self.assertEqual(set(conflict.unit), {'шт','м'})

    def test_monthly_only_product_gets_forecast_and_no_fabricated_invoices(self):
        data, _ = fixture()
        data.sales = invoice_data().iloc[:0]
        lines = prepare_lines(data)
        data.catalog = build_catalog(data.sales, data.monthly.assign(name='Товар',unit='шт'))
        settings = {k:True for k in ('parameters_confirmed','constraints_confirmed',
                                    'incoming_confirmed','reserve_policy_confirmed')}
        row = build_report(data, lines, settings)['rows'][0]
        self.assertTrue(row['ready'], row['blocks'])
        self.assertEqual(row['examples'], [])
        self.assertGreater(row['forecast_horizon'], 0)
        self.assertTrue(any('Нет детализации' in w for w in row['warnings']))

    def test_unknown_unit_is_visible_but_never_exportable(self):
        data, lines = fixture()
        data.catalog.loc[0,'unit'] = ''
        settings = {k:True for k in ('parameters_confirmed','constraints_confirmed',
                                    'incoming_confirmed','reserve_policy_confirmed')}
        row = build_report(data, lines, settings)['rows'][0]
        self.assertFalse(row['ready'])
        self.assertIsNone(row['quantity'])
        self.assertTrue(any('единица измерения' in b for b in row['blocks']))


class BatchTests(unittest.TestCase):
    def test_repetitions_include_neighbours_below_statistical_threshold(self):
        _, lines = build_invoice_lines(invoice_data())
        result = classify_invoice_line_patterns(lines)
        top = result[result.qty.eq(100)].iloc[0]
        self.assertEqual(top.pattern_class, 'repeating_wholesale_batch')
        self.assertEqual(top.similar_large_count, 3)
        self.assertEqual(top.demand_treatment, 'include_in_regular_demand')

    def test_single_pack_and_rounded_minimum_are_not_one_off_candidates(self):
        _, lines = build_invoice_lines(invoice_data(ordinary=1,batches=(100,)))
        key = ('S','sku','шт')
        for rule in ({'pack_multiple':100}, {'pack_multiple':40,'minimum_order_quantity':90}):
            result = classify_invoice_line_patterns(lines, {key:rule})
            top = result[result.qty.eq(100)].iloc[0]
            self.assertEqual(top.pattern_class, 'ordinary_batch')
            self.assertIn('упаковк', top.explanation)
        self.assertEqual(top.batch_floor, 120)
        # Withdrawing confirmation must restore the original statistical rule.
        reset = classify_invoice_line_patterns(result)
        self.assertEqual(reset[reset.qty.eq(100)].iloc[0].pattern_class, 'candidate_one_off')

    def test_pack_confirmation_and_override_change_robust_forecast(self):
        data, _ = fixture()
        data.sales = invoice_data(ordinary=1, batches=(100,))
        # Put the single bulk line in the most recent completed month.
        data.sales.loc[data.sales.index[:-1], 'date'] = pd.date_range('2024-01-01',periods=40)
        data.sales.loc[data.sales.index[-1], 'date'] = pd.Timestamp('2026-08-10')
        data.monthly['quantity'] = 1.
        data.monthly.loc[data.monthly.month.eq('2026-08-01'),'quantity'] = 100.
        data.constraints.loc[0,'value'] = 100.
        lines = prepare_lines(data)
        unconfirmed = build_report(data, lines)['rows'][0]
        confirmed = build_report(data, lines, {'constraints_confirmed':True})['rows'][0]
        self.assertEqual(unconfirmed['candidate_count'], 1)
        self.assertEqual(confirmed['candidate_count'], 0)
        self.assertEqual(confirmed['full_forecast'], confirmed['robust_forecast'])
        overrides = {'S|sku|шт':{'dataset':data.fingerprint,'pack_multiple':1,'reason':'test correction'}}
        changed = build_report(data, lines, {'constraints_confirmed':True}, overrides)['rows'][0]
        self.assertEqual(changed['candidate_count'], 1)
        self.assertLess(changed['robust_forecast'], changed['full_forecast'])

    def test_minimum_quantity_requires_matching_dataset(self):
        data, _ = fixture()
        data.sales = invoice_data(ordinary=1, batches=(100,))
        data.sales['date'] = pd.date_range('2025-01-01',periods=len(data.sales),freq='D')
        lines = prepare_lines(data)
        overrides = {'S|sku|шт':{'dataset':'old','minimum_order_quantity':1000}}
        row = build_report(data, lines, {'constraints_confirmed':True}, overrides)['rows'][0]
        self.assertEqual(row['candidate_count'], 1)


class CacheTests(unittest.TestCase):
    def test_adapter_and_transitive_analysis_changes_invalidate_both_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'procurement').mkdir()
            (root/'analysis').mkdir()
            for name in ('app.py','procurement/data.py','analysis/orders_analysis.py','analysis/procurement_logic.py'):
                (root/name).write_text('version = 1')
            instance = object.__new__(app.Application)
            instance.state_dir = root
            pipeline = app.pipeline_fingerprint
            with patch('app.pipeline_fingerprint', side_effect=lambda:pipeline(root)):
                def keys():
                    return (app.normalized_cache_path(root,'same-files'),
                            instance.report_cache_path(SimpleNamespace(fingerprint='same-files'),{},{}))
                previous = keys()
                self.assertEqual(previous, keys())
                for name in ('procurement/data.py','analysis/orders_analysis.py','analysis/procurement_logic.py'):
                    (root/name).write_text('version = 2')
                    current = keys()
                    self.assertNotEqual(previous[0], current[0], name)
                    self.assertNotEqual(previous[1], current[1], name)
                    previous = current


if __name__ == '__main__':
    unittest.main()
