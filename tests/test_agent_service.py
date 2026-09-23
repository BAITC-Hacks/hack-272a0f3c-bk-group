"""Server-owned agent turns tested with scripted transport and a disposable ledger."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.client import HTTPException
import io
import json
import os
from pathlib import Path
import shutil
import threading
import traceback
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError
import uuid

from operations import OperationsStore
from procurement.agent import AgentService, ENDPOINT, SESSION_TTL, request_openai
from procurement.agent_settings import OpenAISettings
from procurement.agent_tools import AgentTools


def message(text='Готово.', status='completed'):
    return {'status': status, 'output': [{'type': 'message', 'role': 'assistant',
                                         'content': [{'type': 'output_text', 'text': text}]}]}


def function(name, arguments, call_id=None):
    return {'type': 'function_call', 'call_id': call_id or uuid.uuid4().hex,
            'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}


class ScriptedTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, key, payload, timeout):
        self.calls.append((key, deepcopy(payload), timeout))
        if not self.responses:
            raise AssertionError('Unexpected provider call')
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


class AgentServiceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'outputs'
        root.mkdir(exist_ok=True)
        self.folder = root / ('agent-service-test-' + uuid.uuid4().hex)
        self.folder.mkdir()

        def cleanup():
            self.assertEqual(self.folder.resolve().parent, root.resolve())
            shutil.rmtree(self.folder)

        self.addCleanup(cleanup)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        network = patch('procurement.agent.urlopen', side_effect=AssertionError('Network is forbidden in this test'))
        network.start()
        self.addCleanup(network.stop)
        self.settings = OpenAISettings(self.folder)
        self.settings.configure({'api_key': 'sk-dummy-service-key', 'model': 'test-model', 'persist': False})
        self.store = OperationsStore(self.folder / 'operations.sqlite3')
        self.product = self.store.save_entity('products', {'name': 'PRIVATE_CATALOG_PRODUCT', 'sku': 'CAB', 'unit': 'м', 'price': '2.35'})
        self.report = Mock(return_value={'rows': [{'name': 'PRIVATE_FORECAST_PRODUCT'}]})
        self.tools = AgentTools(self.store, self.report)
        self.conversation = uuid.uuid4().hex

    def service(self, *responses):
        transport = ScriptedTransport(*responses)
        service = AgentService(self.settings, self.tools, transport)
        return service, transport

    def request(self, message_text='Покажи товары', **changes):
        return {'conversation_id': self.conversation, 'request_id': uuid.uuid4().hex,
                'message': message_text, **changes}

    def task_call(self, title='Проверить оплату'):
        return function('prepare_task', {'title': title, 'description': '', 'due_date': ''})

    def test_responses_roundtrip_preserves_reasoning_and_supplies_real_tool_results(self):
        reasoning = {'id': 'rs_test', 'type': 'reasoning', 'summary': [], 'encrypted_content': 'opaque-reasoning'}
        search = function('search_products', {'query': 'CAB', 'limit': 10}, 'call_search')
        service, transport = self.service({'status': 'completed', 'output': [reasoning, search]}, message('Найден кабель.'))
        response = service.chat(self.request())
        self.assertEqual(response['answer'], 'Найден кабель.')
        self.assertEqual(response['steps'], [{'name': 'search_products', 'label': 'Поиск товаров'}])
        self.assertEqual(len(transport.calls), 2)
        first, second = (call[1] for call in transport.calls)
        self.assertFalse(first['store'])
        self.assertFalse(first['parallel_tool_calls'])
        self.assertEqual(first['include'], ['reasoning.encrypted_content'])
        self.assertEqual(first['input'], [{'role': 'user', 'content': 'Покажи товары'}])
        self.assertIn(reasoning, second['input'])
        self.assertIn(search, second['input'])
        output = next(item for item in second['input'] if item.get('type') == 'function_call_output')
        self.assertEqual(output['call_id'], 'call_search')
        result = json.loads(output['output'])
        self.assertEqual(result['items'][0]['id'], self.product['id'])
        self.assertEqual(result['items'][0]['name'], 'PRIVATE_CATALOG_PRODUCT')
        self.assertEqual(service._sessions[self.conversation].history, [
            {'role': 'user', 'content': 'Покажи товары'}, {'role': 'assistant', 'content': 'Найден кабель.'}])
        self.assertTrue(all(0 < call[2] <= 30 for call in transport.calls))
        self.assertEqual(self.store.list_documents(), [])

    def test_same_request_is_cached_and_current_proposal_status_is_refreshed(self):
        service, transport = self.service({'output': [self.task_call()]}, message('Проверьте карточку.'))
        request = self.request('Подготовь задачу')
        first = service.chat(request)
        proposal_id = first['proposals'][0]['proposal_id']
        first['answer'] = 'Client altered response'
        second = service.chat(request)
        self.assertEqual(second['answer'], 'Проверьте карточку.')
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(self.tools.proposals(self.conversation)), 1)
        self.assertEqual(self.store.list_tasks(), [])
        service.act({'conversation_id': self.conversation, 'proposal_id': proposal_id}, 'confirm')
        cached = service.chat(request)
        self.assertEqual(cached['proposals'][0]['status'], 'confirmed')
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertEqual(len(transport.calls), 2)

    def test_request_id_cannot_be_reused_for_different_message(self):
        service, transport = self.service(message())
        request = self.request()
        service.chat(request)
        with self.assertRaisesRegex(ValueError, 'другого поручения'):
            service.chat({**request, 'message': 'Создай платёж'})
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(service._sessions[self.conversation].history), 2)

    def test_client_history_other_provider_and_invalid_inputs_are_rejected_before_transport(self):
        service, transport = self.service()
        invalid = [None, [], self.request(history=[]), self.request(provider='nvidia'),
                   self.request(message_text=' '), self.request(message_text='x' * 4001),
                   self.request(conversation_id='../invalid'), self.request(request_id='short')]
        for request in invalid:
            with self.subTest(request_type=type(request).__name__), self.assertRaises(ValueError):
                service.chat(request)
        self.assertEqual(transport.calls, [])
        self.assertEqual(service._sessions, {})

    def test_unknown_and_confirmation_tools_never_reach_tool_dispatch(self):
        calls = [function(name, {}) for name in ('confirm', 'post', 'execute_sql', 'not_a_tool')]
        service, transport = self.service({'output': calls}, message('Действие недоступно.'))
        before = self.store.bootstrap()
        with patch.object(self.tools, 'call', wraps=self.tools.call) as dispatch:
            response = service.chat(self.request())
            dispatch.assert_not_called()
        outputs = [item for item in transport.calls[1][1]['input'] if item.get('type') == 'function_call_output']
        self.assertEqual(len(outputs), 4)
        self.assertTrue(all('error' in json.loads(item['output']) for item in outputs))
        self.assertEqual(response['steps'], [])
        self.assertEqual(self.store.bootstrap(), before)

    def test_invalid_tool_arguments_return_errors_without_business_mutation(self):
        malformed = []
        for raw in ('{broken json', '[]', '{"query":NaN,"limit":1}', 'x' * 30001):
            malformed.append({**function('search_products', {}), 'arguments': raw})
        service, transport = self.service({'output': malformed}, message('Нужно уточнить параметры.'))
        with patch.object(self.tools, 'call', wraps=self.tools.call) as dispatch:
            service.chat(self.request())
            dispatch.assert_not_called()
        outputs = [item for item in transport.calls[1][1]['input'] if item.get('type') == 'function_call_output']
        self.assertEqual(len(outputs), 4)
        self.assertTrue(all(json.loads(item['output']).get('error') for item in outputs))
        self.assertEqual(self.store.list_documents(), [])
        self.assertEqual(self.store.list_tasks(), [])

    def test_failed_turn_declines_only_its_new_proposals_and_can_be_retried(self):
        old = self.tools.call('prepare_task', {'title': 'Предыдущее предложение', 'description': '', 'due_date': ''}, self.conversation)['proposal']
        service, transport = self.service({'output': [self.task_call('Новое предложение')]},
                                          ValueError('Тестовый отказ транспорта'), message('Повтор выполнен.'))
        request = self.request('Подготовь ещё задачу')
        with self.assertRaisesRegex(ValueError, 'отказ транспорта'):
            service.chat(request)
        proposals = self.tools.proposals(self.conversation)
        self.assertEqual(next(p['status'] for p in proposals if p['proposal_id'] == old['proposal_id']), 'pending')
        created = next(p for p in proposals if p['proposal_id'] != old['proposal_id'])
        self.assertEqual(created['status'], 'declined')
        with self.assertRaises(ValueError):
            service.act({'conversation_id': self.conversation, 'proposal_id': created['proposal_id']}, 'confirm')
        session = service._sessions[self.conversation]
        self.assertEqual(session.history, [])
        self.assertEqual(session.responses, {})
        self.assertFalse(session.lock.locked())
        self.assertEqual(service.chat(request)['answer'], 'Повтор выполнен.')
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(self.store.list_tasks(), [])

    def test_confirmation_is_explicit_unmodifiable_and_recorded_once_in_server_history(self):
        service, transport = self.service({'output': [self.task_call()]}, message('Проверьте карточку.'), message('Задача сохранена.'))
        response = service.chat(self.request('Подготовь задачу'))
        proposal_id = response['proposals'][0]['proposal_id']
        confirmation = {'conversation_id': self.conversation, 'proposal_id': proposal_id}
        with self.assertRaises(ValueError):
            service.act({**confirmation, 'title': 'Подмена'}, 'confirm')
        with self.assertRaises(ValueError):
            service.act({**confirmation, 'conversation_id': uuid.uuid4().hex}, 'confirm')
        self.assertEqual(self.store.list_tasks(), [])
        first = service.act(confirmation, 'confirm')
        self.assertEqual(service.act(confirmation, 'confirm'), first)
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertEqual(len(service._sessions[self.conversation].history), 4)
        service.chat(self.request('Что сохранено?'))
        followup = json.dumps(transport.calls[-1][1]['input'], ensure_ascii=False)
        self.assertIn(first['task']['id'], followup)
        self.assertIn('Карточка подтверждена кнопкой сайта', followup)

    def test_declined_proposals_cannot_be_confirmed_and_actions_require_live_session(self):
        service, _ = self.service({'output': [self.task_call()]}, message('Проверьте карточку.'))
        proposal = service.chat(self.request())['proposals'][0]
        request = {'conversation_id': self.conversation, 'proposal_id': proposal['proposal_id']}
        self.assertEqual(service.act(request, 'decline')['status'], 'declined')
        with self.assertRaises(ValueError):
            service.act(request, 'confirm')
        with self.assertRaises(ValueError):
            service.act(request, 'unknown')
        self.assertEqual(self.store.list_tasks(), [])
        restarted, _ = self.service()
        with self.assertRaisesRegex(ValueError, 'перезапущен'):
            restarted.act(request, 'confirm')

    def test_configuration_and_connection_test_do_not_expose_credentials_or_business_data(self):
        service, transport = self.service(message('OK'))
        public = service.configuration()
        self.assertEqual([p['id'] for p in public['providers']], ['openai'])
        self.assertTrue(public['providers'][0]['ready'])
        self.assertNotIn('sk-dummy-service-key', json.dumps(public))
        self.assertNotIn('api_key', public['settings'])
        result = service.test_connection()
        self.assertTrue(result['ok'])
        self.assertEqual(result['model'], 'test-model')
        self.assertEqual(transport.calls[0][1], {'model': 'test-model', 'store': False, 'max_output_tokens': 256,
                                               'input': [{'role': 'user', 'content': 'Reply with OK.'}]})
        self.report.assert_not_called()
        self.assertEqual(service._sessions, {})
        self.assertNotIn('PRIVATE_CATALOG_PRODUCT', json.dumps(transport.calls[0][1]))
        self.assertNotIn('sk-dummy-service-key', json.dumps(result))
        configured = service.configure({'api_key': '', 'model': 'new-test-model', 'persist': False})
        self.assertEqual(configured['settings']['model'], 'new-test-model')
        self.assertNotIn('sk-dummy-service-key', json.dumps(configured))
        self.assertEqual(len(transport.calls), 1)

    def test_missing_credentials_prevent_chat_and_connection_test(self):
        transport = ScriptedTransport()
        service = AgentService(OpenAISettings(self.folder / 'unconfigured'), self.tools, transport)
        for operation in (lambda: service.chat(self.request()), service.test_connection):
            with self.assertRaisesRegex(ValueError, 'не подключён'):
                operation()
        self.assertEqual(transport.calls, [])

    def test_failed_or_incomplete_connection_test_is_not_reported_as_success(self):
        for status in ('incomplete', 'failed', 'cancelled'):
            with self.subTest(status=status):
                service, _ = self.service(message('Partial response', status=status))
                with self.assertRaises(ValueError):
                    service.test_connection()
        for malformed in (None, [], {}, {'output': None}, {'output': []}):
            with self.subTest(malformed=malformed):
                service, _ = self.service(malformed)
                with self.assertRaises(ValueError):
                    service.test_connection()

    def test_failure_reading_existing_proposals_releases_conversation_lock(self):
        service, transport = self.service(message('Recovered'))
        request = self.request()
        with patch.object(self.tools, 'proposals', side_effect=ValueError('Тестовый отказ хранилища')):
            with self.assertRaisesRegex(ValueError, 'отказ хранилища'):
                service.chat(request)
        self.assertFalse(service._sessions[self.conversation].lock.locked())
        self.assertEqual(transport.calls, [])
        self.assertEqual(service.chat(request)['answer'], 'Recovered')

    def test_same_conversation_chat_and_confirmation_are_blocked_while_turn_is_running(self):
        entered, release = threading.Event(), threading.Event()

        def blocked_transport(key, payload, timeout):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError('Test did not release transport')
            return message('Ответ получен.')

        service = AgentService(self.settings, self.tools, blocked_transport)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(service.chat, self.request())
            try:
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaisesRegex(ValueError, 'предыдущий запрос'):
                    service.chat(self.request('Другой вопрос'))
                with self.assertRaisesRegex(ValueError, 'завершения ответа'):
                    service.act({'conversation_id': self.conversation, 'proposal_id': uuid.uuid4().hex}, 'confirm')
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=5)['answer'], 'Ответ получен.')
        service.transport = ScriptedTransport(message('Следующий ответ.'))
        self.assertEqual(service.chat(self.request())['answer'], 'Следующий ответ.')

    def test_call_budget_failure_declines_prepared_actions(self):
        service, transport = self.service({'output': [self.task_call('Первая'), self.task_call('Вторая')]})
        with patch('procurement.agent.MAX_CALLS', 1), self.assertRaisesRegex(ValueError, 'предел действий'):
            service.chat(self.request())
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual([p['status'] for p in self.tools.proposals(self.conversation)], ['declined'])
        self.assertEqual(self.store.list_tasks(), [])
        self.assertFalse(service._sessions[self.conversation].lock.locked())

    def test_round_budget_bounds_repeated_model_tool_requests(self):
        output = {'output': [function('business_overview', {})]}
        service, transport = self.service(output, output)
        with patch('procurement.agent.MAX_ROUNDS', 2), self.assertRaisesRegex(ValueError, 'слишком много шагов'):
            service.chat(self.request())
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(service._sessions[self.conversation].history, [])

    def test_total_deadline_prevents_another_provider_round(self):
        now = [100.0]
        calls = []

        def slow_transport(key, payload, timeout):
            calls.append(timeout)
            now[0] += 121
            return {'output': [function('business_overview', {})]}

        service = AgentService(self.settings, self.tools, slow_transport)
        with patch('procurement.agent.time.monotonic', side_effect=lambda: now[0]):
            with self.assertRaisesRegex(ValueError, 'слишком много времени'):
                service.chat(self.request())
        self.assertEqual(calls, [30])
        self.assertFalse(service._sessions[self.conversation].lock.locked())

    def test_invalid_provider_results_never_commit_history_and_allow_recovery(self):
        invalid = [None, {'output': None}, {'output': ['invalid']}, {'output': []},
                   message('partial', status='incomplete'), message('partial', status='failed'),
                   {'output': [{**function('business_overview', {}), 'call_id': ''}]}]
        for result in invalid:
            with self.subTest(result=result):
                service, _ = self.service(result, message('Recovered'))
                request = self.request()
                with self.assertRaises(ValueError):
                    service.chat(request)
                self.assertEqual(service._sessions[self.conversation].history, [])
                self.assertEqual(service.chat(request)['answer'], 'Recovered')

    def test_sessions_history_and_request_cache_are_bounded(self):
        service, transport = self.service(*[message() for _ in range(24)])
        for number in range(21):
            service.chat(self.request('Поручение ' + str(number)))
        session = service._sessions[self.conversation]
        self.assertEqual(len(session.history), 12)
        self.assertEqual(len(session.responses), 20)
        self.assertEqual(session.history[0]['content'], 'Поручение 15')
        self.assertEqual(len(transport.calls[-1][1]['input']), 13)
        with patch('procurement.agent.MAX_SESSIONS', 1):
            with self.assertRaisesRegex(ValueError, 'активных диалогов'):
                service.chat(self.request(conversation_id=uuid.uuid4().hex))
            session.touched -= SESSION_TTL + 1
            new_id = uuid.uuid4().hex
            service.chat(self.request(conversation_id=new_id))
            self.assertEqual(set(service._sessions), {new_id})


class OpenAITransportTests(unittest.TestCase):
    def setUp(self):
        self.key = 'sk-dummy-transport-key'
        self.payload = {'model': 'test-model', 'store': False, 'input': [{'role': 'user', 'content': 'Hello'}]}

    def response(self, raw):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = raw
        return response

    def test_adapter_uses_fixed_https_endpoint_bearer_header_and_bounded_read(self):
        expected = message('Ответ')
        response = self.response(json.dumps(expected).encode('utf-8'))
        with patch('procurement.agent.urlopen', return_value=response) as send:
            self.assertEqual(request_openai(self.key, self.payload, timeout=7), expected)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.key)
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        self.assertEqual(json.loads(request.data), self.payload)
        self.assertNotIn(self.key, request.data.decode('utf-8'))
        self.assertEqual(send.call_args.kwargs, {'timeout': 7})
        response.__enter__.return_value.read.assert_called_once_with(2_000_001)

    def test_http_error_messages_and_bodies_cannot_expose_credentials(self):
        for code in (400, 401, 403, 404, 429, 500):
            with self.subTest(status=code):
                body = io.BytesIO(('private body: ' + self.key).encode('utf-8'))
                failure = HTTPError(ENDPOINT, code, self.key, {}, body)
                with patch('procurement.agent.urlopen', side_effect=failure):
                    try:
                        request_openai(self.key, self.payload)
                    except ValueError as error:
                        self.assertNotIn(self.key, ''.join(traceback.format_exception(error)))
                        self.assertNotIn('private body', str(error))
                    else:
                        self.fail('HTTP errors must not produce successful responses')
                self.assertEqual(body.tell(), 0)
                body.close()

    def test_network_failures_are_sanitized(self):
        for failure in (URLError(self.key), TimeoutError(self.key), OSError(self.key), HTTPException(self.key)):
            with self.subTest(failure_type=type(failure).__name__):
                with patch('procurement.agent.urlopen', side_effect=failure):
                    with self.assertRaisesRegex(ValueError, 'OpenAI') as error:
                        request_openai(self.key, self.payload)
                self.assertNotIn(self.key, str(error.exception))

    def test_connection_refused_is_not_reported_as_model_timeout(self):
        import ssl
        failures = [(URLError(ConnectionRefusedError(self.key)), 'Нет соединения'),
                    (URLError(TimeoutError(self.key)), 'Время ожидания'),
                    (TimeoutError(self.key), 'Время ожидания'),
                    (URLError(ssl.SSLCertVerificationError(self.key)), 'защищённое соединение')]
        for failure, expected in failures:
            with self.subTest(reason=expected), patch('procurement.agent.urlopen', side_effect=failure):
                with self.assertRaisesRegex(ValueError, expected) as error:
                    request_openai(self.key, self.payload)
                self.assertNotIn(self.key, str(error.exception))

    def test_malformed_and_oversized_provider_bodies_are_rejected(self):
        bodies = (b'', b'not json', b'\xff', b'[]', b'null', b'{}', b'{"output":{}}', b'x' * 2_000_001)
        for raw in bodies:
            with self.subTest(size=len(raw)):
                with patch('procurement.agent.urlopen', return_value=self.response(raw)):
                    with self.assertRaises(ValueError):
                        request_openai(self.key, self.payload)


if __name__ == '__main__':
    unittest.main()
