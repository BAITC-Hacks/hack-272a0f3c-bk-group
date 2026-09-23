"""Real HTTP routing tests with isolated state and an in-process fake model."""

from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch
import uuid

from app import Application, Handler


FAKE_KEY = 'sk-http-test-only-not-a-real-secret-5f79a042'


def answer(text='Проверьте подготовленную карточку.'):
    return {'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': text}]}]}


def tool_call(name, arguments):
    return {'output': [{'type': 'function_call', 'name': name,
                        'call_id': 'call_' + uuid.uuid4().hex, 'arguments': json.dumps(arguments)}]}


class FakeTransport:
    def __init__(self):
        self.responses = []
        self.calls = []

    def __call__(self, key, payload, timeout=30):
        self.calls.append({'key': key, 'payload': deepcopy(payload), 'timeout': timeout})
        if not self.responses:
            raise AssertionError('Unexpected model call; no network transport is allowed')
        return deepcopy(self.responses.pop(0))


class AgentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'OPENAI_API_KEY': '', 'OPENAI_MODEL': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        root = Path(__file__).resolve().parents[1] / 'outputs'
        root.mkdir(exist_ok=True)
        self.folder = root / ('agent-http-test-' + uuid.uuid4().hex)
        self.folder.mkdir()
        def cleanup_folder():
            self.assertEqual(self.folder.resolve().parent, root.resolve())
            shutil.rmtree(self.folder)
        self.addCleanup(cleanup_folder)
        self.app = Application([], self.folder, history_output_dir=self.folder / 'history')
        self.transport = FakeTransport()
        self.app.agent.transport = self.transport
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.application = self.app
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        def stop_server():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.assertFalse(self.thread.is_alive())
        self.addCleanup(stop_server)
        self.conversation = uuid.uuid4().hex

    def request(self, path, payload=None, *, method=None, headers=None, raw=None):
        method = method or ('GET' if payload is None and raw is None else 'POST')
        body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        actual_headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{self.port}'}
        actual_headers.update(headers or {})
        connection = HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=actual_headers)
            response = connection.getresponse()
            content = response.read()
            self.assertNotIn(FAKE_KEY.encode(), content, 'HTTP response must never expose API credentials')
            self.assertEqual(response.getheader('Content-Type'), 'application/json; charset=utf-8')
            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
            return response.status, json.loads(content)
        finally:
            connection.close()

    def configure(self):
        status, body = self.request('/api/assistant/configure', {'api_key': FAKE_KEY, 'model': 'test-model', 'persist': False})
        self.assertEqual(status, 200, body)
        self.assertTrue(body['settings']['ready'])
        return body

    def chat(self, path='/api/agent/chat', **changes):
        payload = {'message': 'Подготовь поручение', 'conversation_id': self.conversation,
                   'request_id': uuid.uuid4().hex, 'provider': 'openai'}
        payload.update(changes)
        return self.request(path, payload)

    def task_proposal(self):
        self.transport.responses.extend([tool_call('prepare_task', {
            'title': 'Уточнить поставку', 'description': 'Сверить срок с поставщиком', 'due_date': ''}), answer()])
        status, body = self.chat()
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body['proposals']), 1)
        return body['proposals'][0]

    def test_configuration_and_connection_test_keep_key_server_side(self):
        status, initial = self.request('/api/assistant/config')
        self.assertEqual(status, 200)
        self.assertFalse(initial['settings']['ready'])
        status, unconfigured = self.request('/api/assistant/test', {})
        self.assertEqual(status, 400)
        self.assertIn('error', unconfigured)
        self.assertEqual(self.transport.calls, [])
        configured = self.configure()
        self.assertEqual(configured['settings']['source'], 'session')
        self.assertFalse(configured['settings']['persistent'])
        self.assertEqual([item['id'] for item in configured['providers']], ['openai'])
        self.transport.responses.append(answer('OK'))
        status, tested = self.request('/api/assistant/test', {})
        self.assertEqual(status, 200, tested)
        self.assertTrue(tested['ok'])
        sent = self.transport.calls[0]
        self.assertEqual(sent['key'], FAKE_KEY)
        self.assertFalse(sent['payload']['store'])
        self.assertNotIn('tools', sent['payload'])
        self.assertEqual(sent['payload']['input'], [{'role': 'user', 'content': 'Reply with OK.'}])
        status, public = self.request('/api/assistant/config')
        self.assertEqual(status, 200)
        self.assertNotIn('api_key', public['settings'])
        self.assertEqual(list(self.folder.glob('openai-settings*')), [])

    def test_both_chat_routes_use_agent_and_do_not_trust_client_history(self):
        self.configure()
        for path in ('/api/chat', '/api/agent/chat'):
            self.transport.responses.append(answer('Готов помочь.'))
            status, body = self.chat(path)
            self.assertEqual(status, 200, body)
            self.assertEqual(body['answer'], 'Готов помочь.')
            self.assertEqual(body['conversation_id'], self.conversation)
            self.assertEqual(body['proposals'], [])
        calls_before = len(self.transport.calls)
        for changes in ({'history': []}, {'provider': 'nvidia'}, {'message': 'x' * 4001}):
            status, body = self.chat(**changes)
            self.assertEqual(status, 400, body)
        self.assertEqual(len(self.transport.calls), calls_before)

    def test_task_confirm_is_idempotent_and_decline_has_no_write(self):
        self.configure()
        proposal = self.task_proposal()
        self.assertEqual(self.app.operations.list_tasks(), [])
        action = {'conversation_id': self.conversation, 'proposal_id': proposal['proposal_id']}
        first_status, first = self.request('/api/agent/confirm', action)
        second_status, second = self.request('/api/agent/confirm', action)
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(first, second)
        self.assertEqual(first['task']['status'], 'open')
        self.assertEqual(len(self.app.operations.list_tasks()), 1)
        self.assertEqual(self.app.operations.stock(), [])
        self.assertEqual(self.app.operations.money()['balance'], '0.00')
        # A fresh conversation keeps this helper's single-proposal assertion clear.
        self.conversation = uuid.uuid4().hex
        declined = self.task_proposal()
        action = {'conversation_id': self.conversation, 'proposal_id': declined['proposal_id']}
        for _ in range(2):
            status, body = self.request('/api/agent/decline', action)
            self.assertEqual(status, 200, body)
            self.assertEqual(body['status'], 'declined')
        status, body = self.request('/api/agent/confirm', action)
        self.assertEqual(status, 400, body)
        self.assertEqual(len(self.app.operations.list_tasks()), 1)

    def test_document_confirm_saves_exact_draft_once_without_ledger(self):
        self.configure()
        product = self.app.operations.save_entity('products', {'name': 'Товар', 'unit': 'шт', 'price': '7.35'})
        warehouse = self.app.operations.save_entity('warehouses', {'name': 'Склад'})
        args = {'kind': 'retail_sale', 'date': '', 'warehouse_id': warehouse['id'], 'target_warehouse_id': '',
                'counterparty_id': '', 'description': 'Черновик продавцу', 'amount': None,
                'lines': [{'product_id': product['id'], 'quantity': '2', 'price': '7.35', 'role': ''}]}
        self.transport.responses.extend([tool_call('prepare_document', args), answer()])
        status, prepared = self.chat()
        self.assertEqual(status, 200, prepared)
        self.assertEqual(self.app.operations.list_documents(), [])
        proposal = prepared['proposals'][0]
        self.assertEqual(proposal['details']['amount'], '14.70')
        action = {'conversation_id': self.conversation, 'proposal_id': proposal['proposal_id']}
        status, first = self.request('/api/agent/confirm', action)
        repeated_status, repeated = self.request('/api/agent/confirm', action)
        self.assertEqual((status, repeated_status), (200, 200))
        self.assertEqual(first, repeated)
        self.assertEqual(first['document']['status'], 'draft')
        self.assertEqual(first['document']['amount'], '14.70')
        self.assertEqual(len(self.app.operations.list_documents()), 1)
        self.assertEqual(self.app.operations.stock(), [])
        self.assertEqual(self.app.operations.money(), {'balance': '0.00', 'income': '0.00', 'expense': '0.00'})

    def test_external_origin_rejected_before_config_chat_confirm_and_decline(self):
        self.configure()
        proposal = self.task_proposal()
        pending = {'conversation_id': self.conversation, 'proposal_id': proposal['proposal_id']}
        old_credentials = self.app.agent.settings.credentials()
        calls = len(self.transport.calls)
        journal = self.app.operations.journal()
        requests = [('/api/assistant/configure', {'api_key': 'different-fake-key', 'model': 'other', 'persist': False}),
                    ('/api/assistant/test', {}),
                    ('/api/agent/chat', {'message': 'Выполнить', 'conversation_id': uuid.uuid4().hex, 'request_id': uuid.uuid4().hex}),
                    ('/api/agent/confirm', pending), ('/api/agent/decline', pending)]
        for path, payload in requests:
            with self.subTest(path=path):
                status, body = self.request(path, payload, headers={'Origin': 'https://outside.example'})
                self.assertEqual(status, 400, body)
                self.assertIn('источника', body['error'])
        self.assertEqual(self.app.agent.settings.credentials(), old_credentials)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(self.app.operations.journal(), journal)
        self.assertEqual(self.app.operations.list_tasks(), [])
        self.assertEqual(self.app.agent.tools.proposals(self.conversation)[0]['status'], 'pending')
        status, _ = self.request('/api/assistant/config', headers={'Host': 'outside.example'})
        self.assertEqual(status, 400)

    def test_spoofed_confirmation_and_foreign_session_cannot_write(self):
        self.configure()
        proposal = self.task_proposal()
        action = {'conversation_id': self.conversation, 'proposal_id': proposal['proposal_id']}
        for extra in ({'payload': {'title': 'Подмена'}}, {'status': 'posted'}, {'document': {'amount': '1000'}}, {'action': 'post'}):
            with self.subTest(extra=extra):
                status, body = self.request('/api/agent/confirm', {**action, **extra})
                self.assertEqual(status, 400, body)
        foreign = uuid.uuid4().hex
        self.transport.responses.append(answer('Новый диалог.'))
        status, _ = self.chat(conversation_id=foreign)
        self.assertEqual(status, 200)
        status, body = self.request('/api/agent/confirm', {**action, 'conversation_id': foreign})
        self.assertEqual(status, 400, body)
        self.assertEqual(self.app.operations.list_tasks(), [])
        self.assertEqual(self.app.agent.tools.proposals(self.conversation)[0]['status'], 'pending')

    def test_request_body_limits_and_json_shape_reject_before_provider(self):
        self.configure()
        routes = [('/api/agent/chat', 50_001), ('/api/agent/confirm', 50_001),
                  ('/api/assistant/configure', 50_001), ('/api/assistant/test', 50_001), ('/api/chat', 200_001)]
        for path, length in routes:
            with self.subTest(path=path):
                status, body = self.request(path, raw=b'', headers={'Content-Length': str(length)})
                self.assertEqual(status, 400, body)
                self.assertIn('error', body)
        for raw, headers in [(b'[]', {}), (b'null', {}), (b'{bad-json', {}),
                             (b'{"amount":NaN}', {}), (b'{}', {'Content-Type': 'text/plain'}),
                             (b'', {'Content-Length': '0'})]:
            with self.subTest(raw=raw):
                status, body = self.request('/api/agent/confirm', raw=raw, headers=headers)
                self.assertEqual(status, 400, body)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.app.operations.list_documents(), [])
        self.assertEqual(self.app.operations.list_tasks(), [])


if __name__ == '__main__':
    unittest.main()
