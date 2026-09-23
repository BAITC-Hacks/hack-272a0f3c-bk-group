"""Confirmed drafts are isolated from model calls and never post the ledger."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import shutil
import threading
import unittest
import uuid

from operations import DOCUMENT_KINDS, OperationsStore
from procurement.agent_tools import AgentTools


class AgentToolsTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'outputs'
        root.mkdir(exist_ok=True)
        self.folder = root / ('agent-tools-test-' + uuid.uuid4().hex)
        self.folder.mkdir()
        def cleanup():
            self.assertEqual(self.folder.resolve().parent, root.resolve())
            shutil.rmtree(self.folder)
        self.addCleanup(cleanup)
        self.store = OperationsStore(self.folder / 'operations.sqlite3')
        self.product = self.store.save_entity('products', {'name': 'Кабель', 'sku': 'CAB', 'unit': 'м', 'price': '0.01'})
        self.output = self.store.save_entity('products', {'name': 'Комплект', 'sku': 'KIT', 'unit': 'шт', 'price': '0.01'})
        self.warehouse = self.store.save_entity('warehouses', {'name': 'Основной'})
        self.second = self.store.save_entity('warehouses', {'name': 'Второй'})
        self.partner = self.store.save_entity('counterparties', {'name': 'Партнёр', 'type': 'both'})
        self.report = {'as_of': '2026-09-23', 'rows': [
            {'key': f'item-{i}', 'name': f'Позиция {i}', 'sku': str(i), 'unit': 'шт', 'ready': False,
             'quantity': None, 'blocks': ['Нет складского среза'], 'free_stock': float('nan')} for i in range(8)]}
        self.agent = AgentTools(self.store, lambda: self.report)
        self.session = uuid.uuid4().hex

    def arguments(self, kind='receipt', **changes):
        args = {'kind': kind, 'date': '', 'warehouse_id': self.warehouse['id'], 'target_warehouse_id': '',
                'counterparty_id': self.partner['id'], 'description': 'Проверить продавцу',
                'lines': [{'product_id': self.product['id'], 'quantity': '0.5', 'price': '0.01', 'role': ''}], 'amount': None}
        if kind in ('payment_in', 'payment_out'):
            args.update(lines=[], amount='2.35', warehouse_id='')
        if kind == 'transfer':
            args['target_warehouse_id'] = self.second['id']
        if kind in ('production', 'production_order'):
            args['lines'][0]['role'] = 'material'
            args['lines'].append({'product_id': self.output['id'], 'quantity': '1', 'price': '0.01', 'role': 'output'})
        args.update(changes)
        return args

    def prepare(self, args=None):
        return self.agent.call('prepare_document', args or self.arguments(), self.session)['proposal']

    def test_strict_schemas_allowlist_and_json_results(self):
        definitions = self.agent.definitions()
        def check(schema):
            if schema['type'] == 'object':
                self.assertFalse(schema['additionalProperties'])
                self.assertEqual(set(schema['required']), set(schema['properties']))
                for item in schema['properties'].values():
                    check(item)
            elif schema['type'] == 'array':
                check(schema['items'])
        for definition in definitions:
            self.assertEqual(definition['type'], 'function')
            self.assertTrue(definition['strict'])
            check(definition['parameters'])
        for forbidden in ('confirm', 'decline', 'post', 'cancel', 'execute_sql', 'http_request', '__dict__'):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                self.agent.call(forbidden, {}, self.session)
        definitions[0]['parameters']['properties'].clear()
        self.assertTrue(self.agent.definitions()[0]['parameters']['properties'])
        json.dumps(self.agent.call('business_overview', {}, self.session), allow_nan=False)

    def test_preparation_is_read_only_and_preview_is_exact(self):
        before = self.store.bootstrap()
        journal = self.store.journal()
        lines = [*self.arguments()['lines'], {'product_id': self.output['id'], 'quantity': '0.5', 'price': '0.01', 'role': ''}]
        proposal = self.prepare(self.arguments(lines=lines))
        self.assertEqual(proposal['details']['amount'], '0.02')
        self.assertEqual([line['amount'] for line in proposal['details']['lines']], ['0.01', '0.01'])
        self.assertEqual(proposal['details']['lines'][0]['name'], self.product['name'])
        self.assertEqual(proposal['details']['warehouse_name'], 'Основной')
        self.assertEqual(self.store.bootstrap(), before)
        self.assertEqual(self.store.journal(), journal)
        result = self.agent.confirm(self.session, proposal['proposal_id'])
        self.assertEqual(result['document']['status'], 'draft')
        self.assertEqual(result['document']['amount'], '0.02')
        self.assertEqual(self.store.stock(), [])
        self.assertEqual(self.store.money()['balance'], '0.00')

    def test_server_payload_cannot_be_changed_by_preview_mutation(self):
        args = self.arguments()
        proposal = self.prepare(args)
        identifier = proposal['proposal_id']
        args['lines'][0]['quantity'] = '999'
        proposal['details']['lines'][0]['quantity'] = '777'
        self.agent.proposals(self.session)[0]['details']['lines'][0]['quantity'] = '555'
        result = self.agent.confirm(self.session, identifier)
        self.assertEqual(result['document']['lines'][0]['quantity'], '0.5')
        result['document']['amount'] = '1000.00'
        self.assertEqual(self.agent.confirm(self.session, identifier)['document']['amount'], '0.01')

    def test_parallel_confirm_creates_one_draft_and_returns_same_result(self):
        identifier = self.prepare()['proposal_id']
        barrier = threading.Barrier(4)
        def confirm():
            barrier.wait(timeout=5)
            return self.agent.confirm(self.session, identifier)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(confirm) for _ in range(4)]
            results = [f.result(timeout=10) for f in futures]
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.store.list_documents()), 1)
        self.assertEqual(self.agent.proposals(self.session)[0]['status'], 'confirmed')
        self.assertEqual(self.store.stock(), [])

    def test_foreign_conversation_decline_expiry_and_restart(self):
        clock = [100.0]
        self.agent._clock = lambda: clock[0]
        identifier = self.prepare()['proposal_id']
        for method in (self.agent.confirm, self.agent.decline):
            with self.assertRaises(ValueError):
                method('foreign-conversation', identifier)
        self.assertEqual(self.agent.proposals('foreign-conversation'), [])
        self.assertEqual(self.agent.decline(self.session, identifier)['status'], 'declined')
        self.assertEqual(self.agent.decline(self.session, identifier)['status'], 'declined')
        with self.assertRaises(ValueError):
            self.agent.confirm(self.session, identifier)
        another = self.prepare()['proposal_id']
        with self.assertRaises(ValueError):
            AgentTools(self.store, lambda: None).confirm(self.session, another)
        clock[0] += self.agent.TTL_SECONDS
        with self.assertRaises(ValueError):
            self.agent.confirm(self.session, another)
        self.assertEqual(self.agent.proposals(self.session), [])
        self.assertEqual(self.store.list_documents(), [])

    def test_session_cap_and_expiry_release_memory(self):
        clock = [1.0]
        self.agent._clock = lambda: clock[0]
        for _ in range(20):
            self.prepare()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(len(self.agent.proposals(self.session)), 20)
        clock[0] += self.agent.TTL_SECONDS
        self.prepare()
        self.assertEqual(len(self.agent.proposals(self.session)), 1)

    def test_reference_version_conflict_prevents_all_writes(self):
        for kind, entity in [('products', self.product), ('warehouses', self.warehouse), ('counterparties', self.partner)]:
            with self.subTest(kind=kind):
                proposal = self.prepare()
                current = next(row for row in self.store.list_entities(kind) if row['id'] == entity['id'])
                self.store.save_entity(kind, {**current, 'name': current['name'] + ' изменён'})
                journal_count = len(self.store.journal())
                with self.assertRaisesRegex(ValueError, 'Справочник изменился'):
                    self.agent.confirm(self.session, proposal['proposal_id'])
                self.assertEqual(len(self.store.journal()), journal_count)
                self.assertEqual(self.store.list_documents(), [])

    def test_prepare_validates_numbers_missing_entities_and_unknown_fields(self):
        invalid = []
        for field, value in [('id', 'existing-id'), ('status', 'posted'), ('execute', 'post')]:
            invalid.append(self.arguments(**{field: value}))
        invalid.extend([self.arguments(warehouse_id='missing'), self.arguments(counterparty_id='missing'),
                        self.arguments(warehouse_id=''), self.arguments(counterparty_id=''), self.arguments(lines=[]),
                        self.arguments('payment_in', amount='0'), self.arguments('payment_in', amount='NaN'),
                        self.arguments('payment_in', amount=100), self.arguments('receipt', amount='3.00'),
                        self.arguments(date='2026-02-30'), self.arguments('transfer', target_warehouse_id=self.warehouse['id'])])
        for quantity in ('-1', '0', '1e3', '0.0000001', 'NaN', True, 1):
            args = self.arguments()
            args['lines'][0]['quantity'] = quantity
            invalid.append(args)
        for price in ('0.001', 'Infinity', '-1', 0.01):
            args = self.arguments()
            args['lines'][0]['price'] = price
            invalid.append(args)
        args = self.arguments()
        args['lines'][0]['product_id'] = 'missing'
        invalid.append(args)
        args = self.arguments()
        args['lines'][0]['name'] = 'Model forged name'
        invalid.append(args)
        args = self.arguments()
        args['lines'] *= 2
        invalid.append(args)
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.prepare(args)
        self.assertEqual(self.agent.proposals(self.session), [])
        self.assertEqual(self.store.list_documents(), [])

    def test_every_document_kind_creates_only_draft_and_production_total_matches(self):
        for kind in DOCUMENT_KINDS:
            proposal = self.prepare(self.arguments(kind))
            result = self.agent.confirm(self.session, proposal['proposal_id'])
            self.assertEqual(result['document']['kind'], kind)
            self.assertEqual(result['document']['status'], 'draft')
            self.assertEqual(result['document']['amount'], proposal['details']['amount'])
        self.assertEqual(len(self.store.list_documents()), 18)
        self.assertEqual(self.store.stock(), [])
        self.assertEqual(self.store.money()['balance'], '0.00')

    def test_task_prepares_then_saves_only_after_confirmation(self):
        args = {'title': 'Позвонить поставщику', 'description': 'Уточнить сроки', 'due_date': '2026-10-01'}
        proposal = self.agent.call('prepare_task', args, self.session)['proposal']
        self.assertEqual(self.store.list_tasks(), [])
        result = self.agent.confirm(self.session, proposal['proposal_id'])
        self.assertEqual(result['task']['title'], args['title'])
        self.assertEqual(result['task']['status'], 'open')
        self.assertEqual(self.agent.confirm(self.session, proposal['proposal_id']), result)
        self.assertEqual(len(self.store.list_tasks()), 1)
        with self.assertRaises(ValueError):
            self.agent.decline(self.session, proposal['proposal_id'])

    def test_bounded_reads_and_forecast_unknowns(self):
        for i in range(12):
            self.store.save_entity('products', {'name': f'Каталог {i}', 'unit': 'шт', 'price': '1'})
        products = self.agent.call('search_products', {'query': 'Каталог', 'limit': 10}, self.session)
        self.assertEqual(products['returned'], 10)
        self.assertEqual(products['matched'], 12)
        self.assertTrue(products['truncated'])
        with self.assertRaises(ValueError):
            self.agent.call('search_products', {'query': '', 'limit': 11}, self.session)
        with self.assertRaises(ValueError):
            self.agent.call('search_products', {'query': 'x' * 121, 'limit': 1}, self.session)
        stock = self.agent.call('product_stock', {'product_id': self.product['id'], 'warehouse_id': '', 'limit': 20}, self.session)
        self.assertEqual(len(stock['items']), 2)
        self.assertTrue(all(row['quantity'] == '0' for row in stock['items']))
        procurement = self.agent.call('search_procurement', {'query': '', 'limit': 5}, self.session)
        self.assertEqual(procurement['returned'], 5)
        self.assertTrue(procurement['truncated'])
        self.assertIsNone(procurement['items'][0]['quantity'])
        self.assertIsNone(procurement['items'][0]['free_stock'])
        self.assertFalse(procurement['items'][0]['ready'])
        self.assertEqual(procurement['items'][0]['blocks'], ['Нет складского среза'])
        json.dumps(procurement, allow_nan=False)

    def test_document_reads_paginate_lines_without_mutating_stored_document(self):
        products = [self.store.save_entity('products', {'name': f'Товар {i}', 'unit': 'шт', 'price': '1'}) for i in range(22)]
        doc = self.store.save_document({'kind': 'stock_in', 'warehouse_id': self.warehouse['id'], 'lines': [
            {'product_id': product['id'], 'quantity': '1'} for product in products]})
        first = self.agent.call('document_detail', {'document_id': doc['id'], 'offset': 0, 'limit': 20}, self.session)
        second = self.agent.call('document_detail', {'document_id': doc['id'], 'offset': 20, 'limit': 20}, self.session)
        self.assertEqual(first['returned'], 20)
        self.assertTrue(first['truncated'])
        self.assertEqual(second['returned'], 2)
        self.assertFalse(second['truncated'])
        self.assertEqual(len(self.store.get_document(doc['id'])['lines']), 22)
        documents = self.agent.call('list_documents', {'query': '', 'kind': '', 'status': 'draft',
                                    'date_from': '', 'date_to': '', 'limit': 20}, self.session)
        self.assertEqual(documents['items'][0]['id'], doc['id'])
        self.assertNotIn('lines', documents['items'][0])


if __name__ == '__main__':
    unittest.main()
