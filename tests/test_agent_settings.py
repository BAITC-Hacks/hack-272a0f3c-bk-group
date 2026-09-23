"""Credential isolation, persistence and validation using dummy secrets only."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import traceback
import unittest
from unittest.mock import patch
import uuid

from procurement.agent_settings import OpenAISettings


class AgentSettingsTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'outputs'
        root.mkdir(exist_ok=True)
        self.folder = root / ('agent-settings-test-' + uuid.uuid4().hex)
        self.folder.mkdir()
        self.state_dir = self.folder / 'state'
        self.file = self.state_dir / 'openai-settings.json'

        def cleanup():
            if self.folder.resolve().parent != root.resolve():
                raise AssertionError('Test cleanup escaped workspace')
            shutil.rmtree(self.folder)

        self.addCleanup(cleanup)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    @contextmanager
    def mock_crypto(self):
        records = {}

        def encrypt(cleartext):
            token = ('opaque-test-ciphertext-' + uuid.uuid4().hex).encode('ascii')
            records[token] = cleartext
            return token

        with patch('procurement.agent_settings._storage_available', return_value=True), \
                patch('procurement.agent_settings._encrypt', side_effect=encrypt), \
                patch('procurement.agent_settings._decrypt', side_effect=lambda value: records[value]):
            yield records

    def configure(self, settings, key='sk-dummy-test-secret', model='test-model', persist=False):
        return settings.configure({'api_key': key, 'model': model, 'persist': persist})

    def test_empty_settings_and_missing_model_are_not_ready(self):
        settings = OpenAISettings(self.state_dir)
        self.assertEqual(settings.credentials(), ('', ''))
        self.assertEqual(settings.public()['source'], 'none')
        self.assertFalse(settings.public()['ready'])
        os.environ['OPENAI_API_KEY'] = 'sk-dummy-env-key'
        self.assertEqual(settings.credentials(), ('sk-dummy-env-key', ''))
        self.assertFalse(settings.public()['ready'])
        self.assertEqual(settings.public()['model'], '')
        self.assertEqual(settings.public()['source'], 'environment')
        self.assertFalse(self.state_dir.exists())

    def test_session_settings_are_private_do_not_write_and_do_not_change_environment(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-dummy-env-key', 'OPENAI_MODEL': 'env-model'}):
            settings = OpenAISettings(self.state_dir)
            self.assertEqual(settings.credentials(), ('sk-dummy-env-key', 'env-model'))
            public = self.configure(settings)
            self.assertTrue(public['ready'])
            self.assertEqual(public['source'], 'session')
            self.assertFalse(public['persistent'])
            self.assertEqual(settings.credentials(), ('sk-dummy-test-secret', 'test-model'))
            self.assertEqual(os.environ['OPENAI_API_KEY'], 'sk-dummy-env-key')
            self.assertEqual(os.environ['OPENAI_MODEL'], 'env-model')
            self.assertNotIn('sk-dummy-test-secret', json.dumps(public))
            self.assertNotIn('sk-dummy-test-secret', repr(settings))
            self.assertNotIn('api_key', public)
            self.assertFalse(self.state_dir.exists())

    def test_empty_or_omitted_key_can_only_reuse_an_existing_key(self):
        settings = OpenAISettings(self.state_dir)
        with self.assertRaises(ValueError):
            self.configure(settings, key='')
        self.configure(settings)
        self.configure(settings, key='', model='second-model')
        self.assertEqual(settings.credentials(), ('sk-dummy-test-secret', 'second-model'))
        settings.configure({'model': 'ft:test-model:organization:custom:123'})
        self.assertEqual(settings.credentials()[1], 'ft:test-model:organization:custom:123')

    def test_invalid_payload_key_model_and_persistence_are_rejected_atomically(self):
        settings = OpenAISettings(self.state_dir)
        self.configure(settings)
        expected = settings.credentials()
        for key in (None, 42, True, ' ', 'x' * 513, 'sk-dummy\nsecret', 'sk-dummy\rsecret'):
            with self.subTest(key_type=type(key).__name__, length=len(key) if isinstance(key, str) else None):
                with self.assertRaises(ValueError):
                    self.configure(settings, key=key)
                self.assertEqual(settings.credentials(), expected)
        for model in ('', ' ', 'x' * 101, 'https://api.openai.com/v1', 'model/name',
                      'model?query', 'model\nname', 'sk-dummy-pasted-key', None, 17):
            with self.subTest(model=model):
                with self.assertRaises(ValueError):
                    self.configure(settings, model=model)
                self.assertEqual(settings.credentials(), expected)
        for value in (None, [], 'settings', 17):
            with self.subTest(payload=value), self.assertRaises(ValueError):
                settings.configure(value)
        for value in (None, 0, 1, 'false', 'true'):
            with self.subTest(persist=value), self.assertRaises(ValueError):
                self.configure(settings, persist=value)
        self.assertEqual(settings.credentials(), expected)
        self.assertFalse(self.state_dir.exists())

    def test_boundaries_are_accepted_and_accidental_secret_model_is_not_exposed(self):
        settings = OpenAISettings(self.state_dir)
        self.configure(settings, key='x' * 512, model='m' * 100)
        self.assertTrue(settings.public()['ready'])
        with self.assertRaises(ValueError):
            self.configure(settings, key='same-value', model='same-value')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-dummy-env-key', 'OPENAI_MODEL': 'sk-dummy-env-key'}):
            settings = OpenAISettings(self.folder / 'another-state')
            self.assertFalse(settings.public()['ready'])
            self.assertNotIn('sk-dummy-env-key', json.dumps(settings.public()))

    def test_persistent_settings_encrypt_entire_record_and_override_environment_on_reload(self):
        with self.mock_crypto() as records, patch.dict(os.environ, {
                'OPENAI_API_KEY': 'sk-dummy-env-key', 'OPENAI_MODEL': 'env-model'}):
            settings = OpenAISettings(self.state_dir)
            public = self.configure(settings, persist=True)
            self.assertTrue(public['persistent'])
            self.assertEqual(public['source'], 'local')
            self.assertEqual(public['warning'], '')
            disk = self.file.read_text(encoding='utf-8')
            self.assertNotIn('sk-dummy-test-secret', disk)
            self.assertNotIn('test-model', disk)
            self.assertEqual(json.loads(next(iter(records.values()))),
                             {'api_key': 'sk-dummy-test-secret', 'model': 'test-model'})
            reloaded = OpenAISettings(self.state_dir)
            self.assertEqual(reloaded.credentials(), settings.credentials())
            self.assertTrue(reloaded.public()['ready'])
            self.assertEqual(reloaded.public()['source'], 'local')
            self.assertEqual(list(self.state_dir.iterdir()), [self.file])

    def test_session_override_preserves_previously_saved_credentials_for_next_start(self):
        with self.mock_crypto():
            settings = OpenAISettings(self.state_dir)
            self.configure(settings, persist=True)
            original_file = self.file.read_bytes()
            self.configure(settings, key='sk-dummy-session-key', model='session-model')
            self.assertFalse(settings.public()['persistent'])
            self.assertEqual(settings.public()['source'], 'session')
            self.assertEqual(settings.credentials(), ('sk-dummy-session-key', 'session-model'))
            self.assertEqual(self.file.read_bytes(), original_file)
            self.assertEqual(OpenAISettings(self.state_dir).credentials(), ('sk-dummy-test-secret', 'test-model'))

    def test_failed_atomic_save_preserves_active_and_saved_values_and_removes_temporary_file(self):
        with self.mock_crypto():
            settings = OpenAISettings(self.state_dir)
            self.configure(settings, persist=True)
            original_file = self.file.read_bytes()
            self.configure(settings, key='sk-dummy-session-key', model='session-model')
            failed_key = 'sk-dummy-failed-secret'
            with patch('procurement.agent_settings.os.replace', side_effect=OSError(failed_key)):
                try:
                    self.configure(settings, key=failed_key, model='failed-model', persist=True)
                except ValueError as error:
                    self.assertNotIn(failed_key, ''.join(traceback.format_exception(error)))
                else:
                    self.fail('A failed replacement must reject the save')
            self.assertEqual(settings.credentials(), ('sk-dummy-session-key', 'session-model'))
            self.assertEqual(self.file.read_bytes(), original_file)
            self.assertEqual(list(self.state_dir.iterdir()), [self.file])

    def test_encrypt_failure_never_writes_plaintext_and_keeps_previous_session(self):
        with self.mock_crypto():
            settings = OpenAISettings(self.state_dir)
            self.configure(settings)
            with patch('procurement.agent_settings._encrypt', side_effect=RuntimeError('sk-dummy-secret-from-os')):
                with self.assertRaises(ValueError) as error:
                    self.configure(settings, key='sk-dummy-replacement', persist=True)
                self.assertNotIn('sk-dummy-secret-from-os', str(error.exception))
            self.assertEqual(settings.credentials(), ('sk-dummy-test-secret', 'test-model'))
            self.assertFalse(self.state_dir.exists())

    def test_corrupt_settings_warn_without_leaking_and_fall_back_to_environment(self):
        self.state_dir.mkdir()
        corrupt_inputs = (b'{not-json', b'[]', b'x' * 16_385,
                          b'{"version":1,"protection":"windows-dpapi-current-user","ciphertext":"%%%"}',
                          b'{"version":1,"protection":"windows-dpapi-current-user","ciphertext":"YWJj"}')
        with self.mock_crypto(), patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-dummy-env-key', 'OPENAI_MODEL': 'env-model'}):
            for content in corrupt_inputs:
                with self.subTest(size=len(content)):
                    self.file.write_bytes(content)
                    settings = OpenAISettings(self.state_dir)
                    public = settings.public()
                    self.assertTrue(public['warning'])
                    self.assertEqual(settings.credentials(), ('sk-dummy-env-key', 'env-model'))
                    self.assertEqual(public['source'], 'environment')
                    self.assertNotIn('ciphertext', json.dumps(public))
                    self.assertNotIn('sk-dummy-env-key', json.dumps(public))
            self.configure(settings, persist=True)
            self.assertEqual(settings.public()['warning'], '')
            self.assertEqual(OpenAISettings(self.state_dir).credentials(), ('sk-dummy-test-secret', 'test-model'))

    def test_unavailable_dpapi_allows_only_memory_configuration(self):
        with patch('procurement.agent_settings._storage_available', return_value=False):
            settings = OpenAISettings(self.state_dir)
            self.assertFalse(settings.public()['storage_available'])
            self.configure(settings)
            with self.assertRaisesRegex(ValueError, 'сеансе'):
                self.configure(settings, key='sk-dummy-new-secret', persist=True)
            self.assertEqual(settings.credentials(), ('sk-dummy-test-secret', 'test-model'))
            self.assertFalse(self.state_dir.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI roundtrip')
    def test_windows_current_user_dpapi_roundtrip_with_dummy_secret(self):
        settings = OpenAISettings(self.state_dir)
        if not settings.public()['storage_available']:
            self.skipTest('Windows DPAPI is unavailable')
        self.configure(settings, key='sk-dummy-dpapi-roundtrip', model='test-dpapi-model', persist=True)
        disk = self.file.read_bytes()
        self.assertNotIn(b'sk-dummy-dpapi-roundtrip', disk)
        self.assertNotIn(b'test-dpapi-model', disk)
        self.assertEqual(OpenAISettings(self.state_dir).credentials(), ('sk-dummy-dpapi-roundtrip', 'test-dpapi-model'))


if __name__ == '__main__':
    unittest.main()
