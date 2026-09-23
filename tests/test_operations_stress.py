"""Synthetic concurrency and ledger edge cases in isolated temporary databases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal
import sqlite3
import json
import threading
import unittest

import test_operations as operation_fixtures


class OperationsStressTests(unittest.TestCase):
    setUp = operation_fixtures.OperationsTests.setUp
    doc = operation_fixtures.OperationsTests.doc
    post = operation_fixtures.OperationsTests.post
    balance = operation_fixtures.OperationsTests.balance

    def simultaneous(self, *calls):
        barrier = threading.Barrier(len(calls))
        def run(call):
            barrier.wait(timeout=5)
            try:
                return 'ok', call()
            except ValueError as error:
                return 'rejected', str(error)
        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            pending = [executor.submit(run, call) for call in calls]
            return [future.result(timeout=20) for future in pending]

    def test_simultaneous_double_post_creates_one_movement_and_one_audit_event(self):
        document = self.doc('receipt', '.123456')
        results = self.simultaneous(*[lambda: self.store.transition(document['id']) for _ in range(4)])
        self.assertEqual([status for status, _ in results].count('ok'), 1)
        self.assertEqual(self.balance(), Decimal('.123456'))
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM stock_movements WHERE document_id=?',
                (document['id'],)).fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM audit WHERE action='post' AND entity_id=?",
                (document['id'],)).fetchone()[0], 1)

    def test_cancel_receipt_racing_consumption_is_serialized_without_negative_balance(self):
        receipt = self.post('receipt', '1')
        shipment = self.doc('shipment', '1')
        results = self.simultaneous(lambda: self.store.transition(receipt['id'], 'cancel'),
                                    lambda: self.store.transition(shipment['id']))
        self.assertEqual([status for status, _ in results].count('ok'), 1)
        self.assertEqual(self.balance(), Decimal(0))
        statuses = (self.store.get_document(receipt['id'])['status'],
                    self.store.get_document(shipment['id'])['status'])
        self.assertIn(statuses, [('cancelled', 'draft'), ('posted', 'posted')])

    def test_simultaneous_versioned_draft_edits_do_not_lose_updates(self):
        document = self.doc('receipt', '1')
        results = self.simultaneous(lambda: self.store.save_document({**document, 'description': 'A'}),
                                    lambda: self.store.save_document({**document, 'description': 'B'}))
        self.assertEqual([status for status, _ in results].count('ok'), 1)
        latest = self.store.get_document(document['id'])
        self.assertEqual(latest['version'], document['version'] + 1)
        self.assertIn(latest['description'], ('A', 'B'))

    def test_omitting_version_cannot_overwrite_another_users_draft(self):
        document = self.doc('receipt', '1')
        changed = self.store.save_document({**document, 'description': 'new version'})
        stale = {key: value for key, value in document.items() if key != 'version'}
        with self.assertRaises(ValueError):
            self.store.save_document({**stale, 'description': 'stale overwrite'})
        self.assertEqual(self.store.get_document(document['id'])['description'], changed['description'])

    def test_draft_version_must_be_an_integer_not_boolean_or_string(self):
        document = self.doc('receipt', '1')
        for version in (True, False, '1', 1.0):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.store.save_document({**document, 'version': version, 'description': 'invalid version'})
        self.assertEqual(self.store.get_document(document['id'])['version'], 1)

    def test_stale_intent_cannot_post_an_unreviewed_draft_revision(self):
        document = self.doc('receipt', '1')
        changed = self.store.save_document({**document, 'lines': [
            {'product_id': self.product['id'], 'quantity': '100'}]})
        before_audit = len(self.store.journal())
        with self.assertRaises(ValueError):
            self.store.transition(document['id'], expected_version=document['version'])
        self.assertEqual(self.balance(), 0)
        self.assertEqual(len(self.store.journal()), before_audit)
        self.assertEqual(self.store.get_document(document['id'])['status'], 'draft')
        self.store.transition(changed['id'], expected_version=changed['version'])
        self.assertEqual(self.balance(), Decimal('100'))

    def test_transition_rejects_noninteger_revision_and_stale_cancel(self):
        document = self.doc('receipt', '1')
        for version in (True, '1', 1.0):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.store.transition(document['id'], expected_version=version)
        posted = self.store.transition(document['id'], expected_version=document['version'])
        with self.assertRaises(ValueError):
            self.store.transition(posted['id'], 'cancel', expected_version=document['version'])
        self.assertEqual(self.balance(), 1)
        self.store.transition(posted['id'], 'cancel', expected_version=posted['version'])
        self.assertEqual(self.balance(), 0)

    def test_transfer_cannot_reverse_goods_already_consumed_at_destination(self):
        self.post('stock_in', '.000003')
        transfer = self.post('transfer', '.000002', target_warehouse_id=self.second['id'])
        self.post('shipment', '.000001', warehouse_id=self.second['id'])
        before = self.store.stock()
        journal_count = len(self.store.journal())
        with self.assertRaises(ValueError):
            self.store.transition(transfer['id'], 'cancel')
        self.assertEqual(self.store.stock(), before)
        self.assertEqual(len(self.store.journal()), journal_count)
        self.assertEqual(self.balance(), Decimal('.000001'))
        self.assertEqual(self.balance(warehouse=self.second), Decimal('.000001'))

    def test_consumed_fractional_output_prevents_partial_production_reversal(self):
        self.post('stock_in', '.000003')
        production = self.post('production', lines=[
            {'product_id': self.product['id'], 'quantity': '.000002', 'role': 'material'},
            {'product_id': self.output['id'], 'quantity': '.000003', 'role': 'output'}])
        self.post('shipment', lines=[{'product_id': self.output['id'], 'quantity': '.000001'}])
        before = self.store.stock()
        with self.assertRaises(ValueError):
            self.store.transition(production['id'], 'cancel')
        self.assertEqual(self.store.stock(), before)
        self.assertEqual(self.balance(), Decimal('.000001'))
        self.assertEqual(self.balance(product=self.output), Decimal('.000002'))

    def test_concurrent_physical_counts_apply_each_target_to_live_balance(self):
        self.post('stock_in', '10')
        one, three = self.doc('inventory', '1'), self.doc('inventory', '3')
        results = self.simultaneous(lambda: self.store.transition(one['id']),
                                    lambda: self.store.transition(three['id']))
        self.assertEqual([status for status, _ in results].count('ok'), 2)
        self.assertIn(self.balance(), (Decimal(1), Decimal(3)))

    def test_concurrent_cash_spending_does_not_overdraw(self):
        self.post('payment_in', '.03')
        first, second = self.doc('payment_out', '.02'), self.doc('payment_out', '.02')
        results = self.simultaneous(lambda: self.store.transition(first['id']),
                                    lambda: self.store.transition(second['id']))
        self.assertEqual([status for status, _ in results].count('ok'), 1)
        self.assertEqual(self.store.money()['balance'], '0.01')

    def test_duplicate_draft_number_creation_is_atomic(self):
        results = self.simultaneous(lambda: self.doc('receipt', '1', number='SYNTHETIC-DUP'),
                                    lambda: self.doc('receipt', '2', number='SYNTHETIC-DUP'))
        self.assertEqual([status for status, _ in results].count('ok'), 1)
        self.assertEqual(len(self.store.list_documents('receipt')), 1)

    def test_future_stock_and_cash_documents_remain_drafts_without_movements(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        for kind in ('receipt', 'stock_in', 'payment_in', 'retail_sale', 'production'):
            with self.subTest(kind=kind):
                extra = {'date': tomorrow}
                if kind == 'production':
                    extra['lines'] = [
                        {'product_id': self.product['id'], 'quantity': '1', 'role': 'material'},
                        {'product_id': self.output['id'], 'quantity': '1', 'role': 'output'}]
                document = self.doc(kind, '1', **extra)
                before_audit = len(self.store.journal())
                with self.assertRaisesRegex(ValueError, 'будущей датой'):
                    self.store.transition(document['id'], expected_version=document['version'])
                self.assertEqual(self.store.get_document(document['id'])['status'], 'draft')
                self.assertEqual(len(self.store.journal()), before_audit)
                self.assertEqual(self.store.stock(), [])
                self.assertEqual(self.store.money()['balance'], '0.00')

    def test_future_planned_orders_and_invoices_do_not_change_current_stock_or_cash(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        for kind in ('purchase_order', 'purchase_invoice', 'sales_order', 'sales_invoice'):
            document = self.post(kind, '100', date=tomorrow)
            self.assertEqual(document['status'], 'posted')
        self.assertEqual(self.store.stock(), [])
        self.assertEqual(self.store.money()['balance'], '0.00')

    def test_stale_product_rename_cannot_roll_back_another_tabs_price(self):
        original = self.store.save_entity('products', {'name': 'Исходное имя', 'sku': 'VERSION',
                                                      'unit': 'шт', 'price': '10'})
        changed = self.store.save_entity('products', {**original, 'price': '20'})
        audit_before = len(self.store.journal())
        with self.assertRaisesRegex(ValueError, 'уже изменена'):
            self.store.save_entity('products', {**original, 'name': 'Устаревшее имя'})
        persisted = next(row for row in self.store.list_entities('products') if row['id'] == original['id'])
        self.assertEqual(persisted, changed)
        self.assertEqual(persisted['price'], '20.00')
        self.assertEqual(persisted['version'], 2)
        self.assertEqual(len(self.store.journal()), audit_before)

    def test_all_entity_types_require_current_integer_version_to_update(self):
        task = self.store.save_task({'title': 'Синтетическая задача'})
        records = [('products', self.product, 'name'), ('counterparties', self.partner, 'name'),
                   ('warehouses', self.warehouse, 'name'), ('tasks', task, 'title')]
        for kind, original, label in records:
            self.assertEqual(original['version'], 1)
            updated = self.store.save_entity(kind, {**original, label: 'Изменено'})
            self.assertEqual(updated['version'], 2)
            for invalid in (None, True, False, '2', 2.0, 1):
                with self.subTest(kind=kind, version=invalid), self.assertRaises(ValueError):
                    self.store.save_entity(kind, {**updated, 'version': invalid, label: 'Нельзя'})
            without_version = {key: value for key, value in updated.items() if key != 'version'}
            with self.subTest(kind=kind, version='missing'), self.assertRaises(ValueError):
                self.store.save_entity(kind, without_version)
            current = next(row for row in self.store.list_entities(kind) if row['id'] == original['id'])
            self.assertEqual(current, updated)

    def test_legacy_entity_versions_are_exposed_without_rewriting_data(self):
        task = self.store.save_task({'title': 'Старая задача'})
        records = [('products', self.product), ('counterparties', self.partner),
                   ('warehouses', self.warehouse), ('tasks', task)]
        legacy_payloads = {}
        with self.store._db(write=True) as database:
            for kind, row in records:
                legacy = {key: value for key, value in row.items() if key != 'version'}
                legacy_payloads[row['id']] = json.dumps(legacy, ensure_ascii=False)
                database.execute('UPDATE entities SET payload=? WHERE id=?',
                                 (legacy_payloads[row['id']], row['id']))
        boot = self.store.bootstrap()
        for kind, original in records:
            with self.subTest(kind=kind):
                listed = next(row for row in self.store.list_entities(kind) if row['id'] == original['id'])
                from_bootstrap = next(row for row in boot[kind] if row['id'] == original['id'])
                self.assertEqual(listed, original)
                self.assertEqual(from_bootstrap, original)
                with self.store._db() as database:
                    self.assertEqual(self.store._entity(database, kind, original['id']), original)
                    persisted = database.execute('SELECT payload FROM entities WHERE id=?',
                                                 (original['id'],)).fetchone()[0]
                    self.assertEqual(persisted, legacy_payloads[original['id']])
                updated = self.store.save_entity(kind, listed)
                self.assertEqual(updated['version'], 2)
        self.assertEqual(self.store.list_tasks()[0]['version'], 2)

    def test_imported_catalogue_versions_preserve_user_edits_on_repeat_import(self):
        rows = [{'key': 'synthetic|versioned|шт', 'name': 'Импорт', 'sku': 'IMP-VERSION', 'unit': 'шт'}]
        self.store.import_products(rows)
        product = next(row for row in self.store.list_entities('products') if row['sku'] == 'IMP-VERSION')
        self.assertEqual(product['version'], 1)
        changed = self.store.save_entity('products', {**product, 'name': 'Своё имя', 'price': '20'})
        result = self.store.import_products(rows)
        self.assertEqual(result['skipped'], 1)
        current = next(row for row in self.store.list_entities('products') if row['id'] == product['id'])
        self.assertEqual(current, changed)
        self.assertEqual(current['version'], 2)

    def test_concurrent_entity_edits_have_exactly_one_winner(self):
        results = self.simultaneous(lambda: self.store.save_entity('products', {**self.product, 'price': '10'}),
                                    lambda: self.store.save_entity('products', {**self.product, 'price': '20'}))
        self.assertEqual([status for status, _ in results].count('ok'), 1)
        current = next(row for row in self.store.list_entities('products') if row['id'] == self.product['id'])
        self.assertEqual(current['version'], 2)
        self.assertIn(current['price'], ('10.00', '20.00'))


if __name__ == '__main__':
    unittest.main()
