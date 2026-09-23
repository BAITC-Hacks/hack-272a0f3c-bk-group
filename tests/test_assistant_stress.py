"""Provider failures are mocked; these tests never send business data externally."""
import json
import unittest
from http.client import IncompleteRead
from unittest.mock import MagicMock, patch

from procurement.assistant import answer


class AssistantStressTests(unittest.TestCase):
    def run_provider(self, body=None, error=None, provider='openai'):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = body
        with patch.dict('os.environ', {provider.upper()+'_API_KEY': 'private-test-key',
                                     provider.upper()+'_MODEL': 'test-model'}, clear=True):
            with patch('procurement.assistant.urlopen', return_value=response, side_effect=error):
                return answer({'provider': provider, 'message': 'Объясни план'}, None,
                              lambda value: json.dumps(value).encode())

    def test_timeout_and_broken_connection_are_actionable_without_secret(self):
        for error in (TimeoutError('private-test-key'), ConnectionResetError('private-test-key'),
                      IncompleteRead(b'private-test-key')):
            with self.subTest(error=type(error).__name__):
                with self.assertRaisesRegex(ValueError, 'Попробуйте ещё раз') as caught:
                    self.run_provider(error=error)
                self.assertNotIn('private-test-key', str(caught.exception))

    def test_non_json_and_invalid_encoding_have_clear_error(self):
        for body in (b'<html>gateway failure</html>', b'\xff\xff'):
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, 'некорректный ответ'):
                    self.run_provider(body)

    def test_incomplete_or_wrong_provider_shapes_do_not_crash(self):
        for provider, result in [('openai', []), ('openai', {'output': [None]}),
                                 ('openai', {'output': [{'type': 'message', 'content': [None]}]}),
                                 ('openai', {'output': None}),
                                 ('nvidia', {'choices': []}),
                                 ('nvidia', {'choices': [{'message': {'content': None}}]})]:
            with self.subTest(provider=provider, result=result):
                with self.assertRaisesRegex(ValueError, 'не вернул текстовый ответ'):
                    self.run_provider(json.dumps(result).encode(), provider=provider)

    def test_stale_product_is_rejected_before_network(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'private-test-key',
                                     'OPENAI_MODEL': 'test-model'}, clear=True):
            with patch('procurement.assistant.urlopen') as call:
                with self.assertRaisesRegex(ValueError, 'больше недоступен'):
                    answer({'message': 'Объясни', 'product_key': 'old'}, {'rows': []},
                           lambda value: json.dumps(value).encode())
                call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
