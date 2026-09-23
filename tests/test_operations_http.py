"""Exercise the operational API through an isolated, ephemeral HTTP server."""

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import threading
import unittest
from urllib.parse import urlencode
import uuid

from app import Handler, ROOT
from operations import OperationsStore


class OperationsHTTPTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / 'outputs'
        test_root.mkdir(exist_ok=True)
        self.temp_path = test_root / ('operations-http-test-' + uuid.uuid4().hex)
        self.temp_path.mkdir()

        def cleanup_directory():
            if self.temp_path.resolve().parent != test_root.resolve():
                raise AssertionError('Test cleanup escaped workspace')
            shutil.rmtree(self.temp_path)

        self.addCleanup(cleanup_directory)
        self.application = SimpleNamespace(
            operations=OperationsStore(self.temp_path / 'operations.sqlite3'),
            report=None,
            busy=False,
        )
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.application = self.application
        self.worker = threading.Thread(
            target=self.server.serve_forever,
            kwargs={'poll_interval': 0.01},
            daemon=True,
        )
        self.worker.start()

        def cleanup_server():
            self.server.shutdown()
            self.server.server_close()
            self.worker.join(timeout=5)
            self.assertFalse(self.worker.is_alive())

        self.addCleanup(cleanup_server)

    def request(self, method, path, payload=None, *, raw=None, headers=None):
        body = raw
        request_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            request_headers.setdefault('Content-Type', 'application/json')
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            content = response.read()
            metadata = dict(response.getheaders())
            if response.getheader('Content-Type', '').startswith('application/json'):
                content = json.loads(content)
            return response.status, content, metadata
        finally:
            connection.close()

    def successful(self, method, path, payload=None):
        status, result, _ = self.request(method, path, payload)
        self.assertEqual(status, 200, result)
        return result

    def entity(self, kind, payload):
        return self.successful('POST', '/api/operations/entity', {'kind': kind, 'payload': payload})

    def draft(self, kind='stock_in'):
        product = self.entity('products', {'name': 'Кабель', 'sku': 'HTTP-CAB', 'unit': 'м', 'price': '2.35'})
        warehouse = self.entity('warehouses', {'name': 'Тестовый склад'})
        return self.successful('POST', '/api/operations/document', {
            'kind': kind,
            'warehouse_id': warehouse['id'],
            'lines': [{'product_id': product['id'], 'quantity': '3', 'price': '2.35'}],
        })

    def test_empty_bootstrap_and_journal_do_not_require_procurement_archives(self):
        result = self.successful('GET', '/api/operations/bootstrap')
        for field in ('products', 'warehouses', 'counterparties', 'tasks', 'stock'):
            self.assertEqual(result[field], [])
        self.assertEqual(result['money']['balance'], '0.00')
        self.assertEqual(self.successful('GET', '/api/operations/documents'), [])
        self.assertEqual(self.successful('GET', '/api/operations/journal'), [])

    def test_transition_requires_reviewed_version_and_rejects_stale_card(self):
        draft=self.draft()
        status,_,_=self.request('POST','/api/operations/transition',{'id':draft['id'],'action':'post'})
        self.assertEqual(status,400)
        changed=self.successful('POST','/api/operations/document',{**draft,'description':'Changed in a second tab'})
        status,_,_=self.request('POST','/api/operations/transition',{
            'id':draft['id'],'action':'post','expected_version':draft['version']})
        self.assertEqual(status,400)
        self.assertEqual(self.successful('GET','/api/operations/bootstrap')['stock'],[])
        self.assertEqual(self.application.operations.get_document(changed['id'])['status'],'draft')

    def test_document_lifecycle_persists_stock_and_reversal_over_http(self):
        draft = self.draft()
        self.assertEqual(draft['status'], 'draft')
        self.assertEqual(self.successful('GET', '/api/operations/bootstrap')['stock'], [])
        query = urlencode({'id': draft['id']})
        self.assertEqual(self.successful('GET', '/api/operations/document?' + query), draft)
        self.assertEqual(self.successful('GET', '/api/operations/documents?kind=stock_in')[0]['id'], draft['id'])
        self.assertEqual(self.successful('GET', '/api/operations/documents?kind=shipment'), [])

        posted = self.successful('POST', '/api/operations/transition', {'id': draft['id'], 'action': 'post', 'expected_version':draft['version']})
        self.assertEqual(posted['status'], 'posted')
        stock = self.successful('GET', '/api/operations/bootstrap')['stock']
        self.assertEqual(len(stock), 1)
        self.assertEqual(stock[0]['quantity'], '3')
        status, failure, _ = self.request('POST', '/api/operations/transition', {'id': draft['id'], 'action': 'post', 'expected_version':posted['version']})
        self.assertEqual(status, 400)
        self.assertTrue(failure['error'])
        self.assertEqual(self.successful('GET', '/api/operations/bootstrap')['stock'], stock)

        cancelled = self.successful('POST', '/api/operations/transition', {'id': draft['id'], 'action': 'cancel', 'expected_version':posted['version']})
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertTrue(all(row['quantity'] == '0' for row in self.successful('GET', '/api/operations/bootstrap')['stock']))
        self.assertGreaterEqual(len(self.successful('GET', '/api/operations/journal')), 5)

    def test_entity_updates_and_tasks_are_visible_in_bootstrap(self):
        partner = self.entity('counterparties', {'name': 'Покупатель', 'type': 'customer'})
        self.entity('counterparties', {**partner, 'name': 'Покупатель после изменения'})
        task = self.entity('tasks', {'title': 'Проверить оплату', 'due_date': '2026-10-01'})
        result = self.successful('GET', '/api/operations/bootstrap')
        self.assertEqual([row['name'] for row in result['counterparties']], ['Покупатель после изменения'])
        self.assertEqual(result['tasks'][0]['id'], task['id'])
        self.assertEqual(result['overview']['tasks_open'], 1)

    def test_import_requires_ready_report_and_never_imports_snapshot_stock(self):
        status, failure, _ = self.request('POST', '/api/operations/import-report', {})
        self.assertEqual(status, 400)
        self.assertIn('загрузите', failure['error'])
        self.application.report = {'rows': [{'key': 'HTTP|A|шт', 'name': 'Тестовый товар', 'sku': 'A', 'unit': 'шт', 'on_hand': 50}]}
        self.application.busy = True
        self.assertEqual(self.request('POST', '/api/operations/import-report', {})[0], 400)
        self.assertEqual(self.successful('GET', '/api/operations/bootstrap')['products'], [])
        self.application.busy = False
        result = self.successful('POST', '/api/operations/import-report', {})
        self.assertEqual(result, {'created': 1, 'updated': 0, 'skipped': 0})
        bootstrap = self.successful('GET', '/api/operations/bootstrap')
        self.assertEqual(bootstrap['products'][0]['external_keys'], ['HTTP|A|шт'])
        self.assertEqual(bootstrap['stock'], [])

    def test_invalid_json_and_non_object_bodies_are_rejected_without_mutation(self):
        for body in (b'{broken json', b'[]', b'null', b'"text"', b''):
            with self.subTest(body=body):
                status, result, _ = self.request('POST', '/api/operations/entity', raw=body)
                self.assertEqual(status, 400)
                self.assertTrue(result['error'])
        self.assertEqual(self.successful('GET', '/api/operations/bootstrap')['products'], [])

    def test_cross_origin_or_remote_host_requests_are_rejected(self):
        for headers in ({'Origin': 'https://example.com'}, {'Host': 'example.com'}):
            for method, path, payload in (
                ('GET', '/api/operations/bootstrap', None),
                ('POST', '/api/operations/entity', {'kind': 'warehouses', 'payload': {'name': 'Untrusted'}}),
            ):
                with self.subTest(headers=headers, method=method):
                    status, result, _ = self.request(method, path, payload, headers=headers)
                    self.assertEqual(status, 400)
                    self.assertTrue(result['error'])
        self.assertEqual(self.successful('GET', '/api/operations/bootstrap')['warehouses'], [])
        origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.assertEqual(self.request('GET', '/api/operations/bootstrap', headers={'Origin': origin})[0], 200)

    def test_unknown_records_and_routes_return_json_errors(self):
        for path in ('/api/operations/document?id=missing', '/api/operations/unknown'):
            with self.subTest(path=path):
                status, result, _ = self.request('GET', path)
                self.assertEqual(status, 400)
                self.assertTrue(result['error'])
        status, result, _ = self.request('POST', '/api/operations/unknown', {})
        self.assertEqual(status, 400)
        self.assertTrue(result['error'])

    def test_static_operational_assets_are_served_with_correct_types(self):
        for path, filename, content_type in (
            ('/', 'index.html', 'text/html'),
            ('/operations.js', 'operations.js', 'text/javascript'),
            ('/operations.css', 'operations.css', 'text/css'),
        ):
            with self.subTest(path=path):
                status, content, headers = self.request('GET', path)
                self.assertEqual(status, 200)
                self.assertEqual(content, (ROOT / 'web' / filename).read_bytes())
                self.assertTrue(headers['Content-Type'].startswith(content_type))
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['Cache-Control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
