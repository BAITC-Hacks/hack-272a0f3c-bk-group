"""Ledger invariants for the local operational workspace."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import shutil
import threading
import unittest
import uuid

from operations import DOCUMENT_KINDS, OperationsStore


class OperationsTests(unittest.TestCase):
    def setUp(self):
        # Keep test fixtures under the writable workspace on managed Windows hosts.
        test_root = Path(__file__).resolve().parents[1] / 'outputs'
        test_root.mkdir(exist_ok=True)
        self.temp_path = test_root / ('operations-test-' + uuid.uuid4().hex)
        self.temp_path.mkdir()
        def cleanup():
            if self.temp_path.resolve().parent != test_root.resolve():
                raise AssertionError('Test cleanup escaped workspace')
            shutil.rmtree(self.temp_path)
        self.addCleanup(cleanup)
        self.path = self.temp_path / 'operations.sqlite3'
        self.store = OperationsStore(self.path)
        self.product = self.store.save_entity('products', {'name': 'Кабель', 'sku': 'CAB', 'unit': 'м', 'price': '2.35'})
        self.output = self.store.save_entity('products', {'name': 'Комплект', 'sku': 'KIT', 'unit': 'шт', 'price': '10'})
        self.warehouse = self.store.save_entity('warehouses', {'name': 'Основной'})
        self.second = self.store.save_entity('warehouses', {'name': 'Второй'})
        self.partner = self.store.save_entity('counterparties', {'name': 'Компания', 'type': 'both'})

    def doc(self, kind, quantity='1', **kwargs):
        payload = {'kind': kind, 'warehouse_id': self.warehouse['id'], 'counterparty_id': self.partner['id'],
                   'lines': [{'product_id': self.product['id'], 'quantity': quantity, 'price': '2.35'}]}
        if kind in ('payment_in', 'payment_out'):
            payload.update(lines=[], amount=quantity)
        payload.update(kwargs)
        return self.store.save_document(payload)

    def post(self, kind, quantity='1', **kwargs):
        doc = self.doc(kind, quantity, **kwargs)
        return self.store.transition(doc['id'])

    def balance(self, product=None, warehouse=None):
        product = product or self.product
        warehouse = warehouse or self.warehouse
        match = next((r for r in self.store.stock() if r['product_id'] == product['id'] and r['warehouse_id'] == warehouse['id']), None)
        return Decimal(match['quantity']) if match else Decimal(0)

    def test_no_synthetic_data(self):
        empty = OperationsStore(self.temp_path / 'empty.sqlite3').bootstrap()
        for kind in ('products', 'counterparties', 'warehouses', 'tasks', 'stock'):
            self.assertEqual(empty[kind], [])
        self.assertEqual(empty['money']['balance'], '0.00')

    def test_drafts_orders_and_invoices_do_not_move_stock_or_cash(self):
        self.doc('receipt', '30')
        for kind in ('purchase_order', 'purchase_invoice', 'sales_order', 'sales_invoice'):
            self.post(kind, '20')
        self.assertEqual(self.store.stock(), [])
        self.assertEqual(self.store.money(), {'balance': '0.00', 'income': '0.00', 'expense': '0.00'})

    def test_receipt_transfer_and_reversal_conserve_exact_quantities(self):
        self.post('receipt', '3.123456')
        transfer = self.post('transfer', '1.123456', target_warehouse_id=self.second['id'])
        self.assertEqual(self.balance(), Decimal('2'))
        self.assertEqual(self.balance(warehouse=self.second), Decimal('1.123456'))
        self.assertEqual(sum(Decimal(r['quantity']) for r in self.store.stock()), Decimal('3.123456'))
        self.store.transition(transfer['id'], 'cancel')
        self.assertEqual(self.balance(), Decimal('3.123456'))
        self.assertEqual(self.balance(warehouse=self.second), Decimal('0'))

    def test_posting_twice_or_cancelling_twice_cannot_duplicate_ledger(self):
        receipt = self.post('receipt', '7')
        with self.assertRaises(ValueError):
            self.store.transition(receipt['id'])
        self.assertEqual(self.balance(), Decimal('7'))
        self.store.transition(receipt['id'], 'cancel')
        for action in ('post', 'cancel'):
            with self.assertRaises(ValueError):
                self.store.transition(receipt['id'], action)
        self.assertEqual(self.balance(), Decimal('0'))

    def test_failed_multi_line_post_leaves_document_and_ledger_unchanged(self):
        self.post('stock_in', '5')
        shipment = self.doc('shipment', lines=[
            {'product_id': self.product['id'], 'quantity': '2'},
            {'product_id': self.output['id'], 'quantity': '1'},
        ])
        journal_count = len(self.store.journal())
        with self.assertRaises(ValueError):
            self.store.transition(shipment['id'])
        self.assertEqual(self.balance(), Decimal('5'))
        self.assertEqual(self.store.get_document(shipment['id'])['status'], 'draft')
        self.assertEqual(len(self.store.journal()), journal_count)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM stock_movements WHERE document_id=?', (shipment['id'],)).fetchone()[0], 0)

    def test_cannot_cancel_receipt_after_stock_consumed(self):
        receipt = self.post('receipt', '5')
        self.post('shipment', '4')
        with self.assertRaises(ValueError):
            self.store.transition(receipt['id'], 'cancel')
        self.assertEqual(self.balance(), Decimal('1'))
        self.assertEqual(self.store.get_document(receipt['id'])['status'], 'posted')

    def test_count_computes_delta_at_posting_and_reversal_is_exact(self):
        self.post('stock_in', '10')
        count = self.doc('inventory', '7')
        self.post('shipment', '2')
        self.store.transition(count['id'])
        self.assertEqual(self.balance(), Decimal('7'))
        self.post('customer_return', '1')
        self.store.transition(count['id'], 'cancel')
        self.assertEqual(self.balance(), Decimal('9'))
        zero = self.post('inventory', '0')
        self.assertEqual(self.balance(), Decimal(0))
        self.store.transition(zero['id'], 'cancel')
        self.assertEqual(self.balance(), Decimal('9'))

    def test_cash_is_exact_and_failed_payment_is_atomic(self):
        receipt = self.post('payment_in', '0.30')
        expense = self.post('payment_out', '0.20')
        self.assertEqual(self.store.money(), {'balance': '0.10', 'income': '0.30', 'expense': '0.20'})
        failed = self.doc('payment_out', '0.11')
        with self.assertRaises(ValueError):
            self.store.transition(failed['id'])
        with self.assertRaises(ValueError):
            self.store.transition(receipt['id'], 'cancel')
        self.assertEqual(self.store.money()['balance'], '0.10')
        self.store.transition(expense['id'], 'cancel')
        self.assertEqual(self.store.money(), {'balance': '0.30', 'income': '0.30', 'expense': '0.00'})
        self.store.transition(receipt['id'], 'cancel')
        self.assertEqual(self.store.money(), {'balance': '0.00', 'income': '0.00', 'expense': '0.00'})

    def test_retail_sale_cash_and_stock_change_together(self):
        self.post('stock_in', '2')
        sale = self.post('retail_sale', '1.5')
        self.assertEqual(sale['amount'], '3.53')
        self.assertEqual(self.balance(), Decimal('0.5'))
        self.assertEqual(self.store.money()['balance'], '3.53')
        self.post('payment_out', '3.53')
        returned = self.doc('retail_return', '1.5')
        with self.assertRaises(ValueError):
            self.store.transition(returned['id'])
        self.assertEqual(self.balance(), Decimal('0.5'))
        self.assertEqual(self.store.get_document(returned['id'])['status'], 'draft')
        self.post('payment_in', '3.53')
        self.store.transition(returned['id'])
        self.assertEqual(self.balance(), Decimal('2'))
        self.assertEqual(self.store.money()['balance'], '0.00')

    def test_production_consumes_materials_and_creates_outputs_atomically(self):
        lines = [{'product_id': self.product['id'], 'quantity': '2', 'role': 'material'},
                 {'product_id': self.output['id'], 'quantity': '1', 'role': 'output'}]
        order = self.post('production_order', lines=lines)
        self.assertEqual(self.store.stock(), [])
        production = self.doc('production', lines=lines)
        with self.assertRaises(ValueError):
            self.store.transition(production['id'])
        self.assertEqual(self.balance(product=self.output), Decimal('0'))
        self.post('stock_in', '3')
        self.store.transition(production['id'])
        self.assertEqual(self.balance(), Decimal('1'))
        self.assertEqual(self.balance(product=self.output), Decimal('1'))
        self.assertEqual(self.store.money()['balance'], '0.00')
        self.store.transition(production['id'], 'cancel')
        self.assertEqual(self.balance(), Decimal('3'))
        self.assertEqual(self.balance(product=self.output), Decimal('0'))
        self.assertEqual(order['amount'], '10.00')

    def test_posted_document_is_immutable_and_stale_draft_detected(self):
        doc = self.doc('receipt', '4')
        changed = self.store.save_document({**doc, 'description': 'Исправлен'})
        with self.assertRaises(ValueError):
            self.store.save_document({**doc, 'description': 'Старый вариант'})
        posted = self.store.transition(changed['id'])
        with self.assertRaises(ValueError):
            self.store.save_document({**posted, 'description': 'Переписать'})
        with self.assertRaises(ValueError):
            self.store.save_entity('products', {**self.product, 'unit': 'кг'})

    def test_validation_missing_references_and_quantities(self):
        for quantity in ('-1', '0', '0.0000001', 'NaN', 'Infinity', True, '1e100000'):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.doc('receipt', quantity)
        for amount in ('-1', '0.001', 'NaN'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.doc('payment_in', amount)
        with self.assertRaises(ValueError):
            self.doc('receipt', warehouse_id='missing')
        with self.assertRaises(ValueError):
            self.doc('receipt', lines=[{'product_id': 'missing', 'quantity': '1'}])
        with self.assertRaises(ValueError):
            self.post('transfer', target_warehouse_id=self.warehouse['id'])
        with self.assertRaises(ValueError):
            self.post('receipt', counterparty_id='')
        with self.assertRaises(ValueError):
            self.post('production', lines=[{'product_id': self.output['id'], 'quantity': '1', 'role': 'output'}])
        with self.assertRaises(ValueError):
            self.post('payment_in', '0')

    def test_import_preserves_external_ids_and_user_edits_without_stock(self):
        rows = [{'key': 'IEK|X|шт', 'name': 'Импорт', 'sku': 'X', 'unit': 'шт', 'on_hand': 100},
                {'key': 'IEK|Y|', 'name': 'Нет единицы', 'sku': 'Y', 'unit': ''}]
        result = self.store.import_products(rows)
        self.assertEqual(result, {'created': 1, 'updated': 0, 'skipped': 1})
        imported = next(p for p in self.store.bootstrap()['products'] if p['sku'] == 'X')
        self.assertEqual(imported['external_keys'], ['IEK|X|шт'])
        self.store.save_entity('products', {**imported, 'name': 'Новое имя', 'price': '7.20'})
        self.assertEqual(self.store.import_products(rows)['created'], 0)
        persisted = OperationsStore(self.path).bootstrap()
        revised = next(p for p in persisted['products'] if p['id'] == imported['id'])
        self.assertEqual(revised['name'], 'Новое имя')
        self.assertEqual(revised['price'], '7.20')
        self.assertEqual(revised['external_keys'], ['IEK|X|шт'])
        self.assertEqual(persisted['stock'], [])

    def test_tasks_and_unique_document_numbers(self):
        task = self.store.save_task({'title': 'Проверить отгрузку', 'due_date': '2026-10-01'})
        self.assertEqual(self.store.bootstrap()['overview']['tasks_open'], 1)
        self.store.save_entity('tasks', {**task, 'status': 'done'})
        self.assertEqual(self.store.bootstrap()['overview']['tasks_open'], 0)
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.doc('receipt', number='R-01')
        with self.assertRaises(ValueError):
            self.doc('receipt', number='R-01')
        self.assertEqual(len(self.store.list_documents('receipt')), 1)

    def test_concurrent_posts_cannot_oversell(self):
        self.post('stock_in', '1')
        first, second = self.doc('shipment'), self.doc('shipment')
        barrier = threading.Barrier(2)
        def post(identifier):
            barrier.wait(timeout=5)
            try:
                self.store.transition(identifier)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(post, doc['id']) for doc in (first, second)]
            results = [f.result(timeout=10) for f in futures]
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.balance(), Decimal('0'))

    def test_all_document_kinds_are_available(self):
        self.assertEqual(len(DOCUMENT_KINDS), 18)
        self.post('stock_in', '8')
        self.post('supplier_return', '2')
        self.post('write_off', '1')
        self.post('customer_return', '2')
        self.assertEqual(self.balance(), Decimal('7'))


if __name__ == '__main__':
    unittest.main()
