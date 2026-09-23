"""Server-only OpenAI settings; persistent secrets use current-user Windows DPAPI."""

import base64
import ctypes
from ctypes import wintypes
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import threading
import uuid


_MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,99}\Z', re.ASCII)
_FILE_LIMIT = 16_384
_READ_WARNING = ('Сохранённые настройки OpenAI не удалось прочитать. '
                 'Введите ключ и модель заново и сохраните их в текущей учётной записи Windows.')
_STORAGE_ERROR = ('Защищённое сохранение доступно только в Windows с DPAPI. '
                  'Выберите использование ключа только в текущем сеансе.')


class _DataBlob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


@lru_cache(maxsize=1)
def _dpapi_libraries():
    if os.name != 'nt':
        raise OSError('DPAPI unavailable')
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [blob_pointer, wintypes.LPCWSTR, blob_pointer,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_pointer]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [blob_pointer, ctypes.c_void_p, blob_pointer,
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_pointer]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _storage_available():
    try:
        _dpapi_libraries()
        return True
    except (OSError, AttributeError):
        return False


def _dpapi(data, *, decrypt=False):
    """Use user scope (never CRYPTPROTECT_LOCAL_MACHINE) without UI prompts."""
    try:
        crypt32, kernel32 = _dpapi_libraries()
        source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        incoming = _DataBlob(len(data), source)
        outgoing = _DataBlob()
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        # A null description avoids allocating a second output buffer on decrypt.
        success = function(ctypes.byref(incoming), None, None, None, None,
                           0x01, ctypes.byref(outgoing))  # CRYPTPROTECT_UI_FORBIDDEN
        try:
            if not success:
                raise ValueError('Операция защищённого хранилища Windows не выполнена.')
            return ctypes.string_at(outgoing.pbData, outgoing.cbData)
        finally:
            if outgoing.pbData:
                if decrypt:
                    ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
                kernel32.LocalFree(outgoing.pbData)
            ctypes.memset(source, 0, len(data))
    except Exception:
        # Never include OS/provider exceptions or their arguments in a response.
        raise ValueError('Операция защищённого хранилища Windows не выполнена.') from None


def _encrypt(data):
    return _dpapi(data)


def _decrypt(data):
    return _dpapi(data, decrypt=True)


def _key(value):
    if not isinstance(value, str) or len(value) > 512 or '\r' in value or '\n' in value:
        raise ValueError('API-ключ должен содержать не более 512 символов без перевода строки.')
    result = value.strip()
    if not result:
        raise ValueError('Введите API-ключ OpenAI.')
    return result


def _model(value):
    if (not isinstance(value, str) or not _MODEL_ID.fullmatch(value)
            or value.startswith('sk-')):
        raise ValueError('Укажите ID модели длиной до 100 символов, без URL, пробелов или API-ключа.')
    return value


class OpenAISettings:
    """Priority: session override, encrypted local settings, then environment.

    ``persist=False`` changes this instance only and leaves an existing encrypted
    file unchanged. ``persistent`` describes the currently active credentials.
    ``credentials`` is for server code only; only ``public`` is safe for the UI.
    No default model is assumed, and configuration makes no network requests.
    """

    def __init__(self, state_dir):
        self._path = Path(state_dir) / 'openai-settings.json'
        self._lock = threading.RLock()
        self._saved = None
        self._session = None
        self._warning = ''
        self._load()

    def __repr__(self):
        return '<OpenAISettings>'

    def _load(self):
        try:
            with self._path.open('rb') as handle:
                content = handle.read(_FILE_LIMIT + 1)
            if len(content) > _FILE_LIMIT or not _storage_available():
                raise ValueError()
            envelope = json.loads(content)
            if (not isinstance(envelope, dict) or envelope.get('version') != 1
                    or envelope.get('protection') != 'windows-dpapi-current-user'
                    or not isinstance(envelope.get('ciphertext'), str)):
                raise ValueError()
            encrypted = base64.b64decode(envelope['ciphertext'], validate=True)
            if not encrypted:
                raise ValueError()
            values = json.loads(_decrypt(encrypted))
            if not isinstance(values, dict):
                raise ValueError()
            key, model = _key(values.get('api_key')), _model(values.get('model'))
            if key == model:
                raise ValueError()
            self._saved = key, model
        except FileNotFoundError:
            pass
        except Exception:
            # Corrupt, inaccessible or other-user files never prevent startup.
            self._warning = _READ_WARNING

    def _active(self):
        if self._session is not None:
            return self._session, 'session', False
        if self._saved is not None:
            return self._saved, 'local', True
        key_value = os.getenv('OPENAI_API_KEY', '')
        model_value = os.getenv('OPENAI_MODEL', '')
        try:
            key = _key(key_value) if key_value else ''
        except ValueError:
            key = ''
        try:
            model = _model(model_value) if model_value else ''
        except ValueError:
            model = ''
        if key and model == key:
            model = ''
        return (key, model), 'environment' if key_value or model_value else 'none', False

    def credentials(self):
        """Return the key/model pair for a server-side provider call only."""
        with self._lock:
            return self._active()[0]

    def public(self):
        with self._lock:
            (key, model), source, persistent = self._active()
            return {'ready': bool(key and model), 'model': model, 'source': source,
                    'persistent': persistent, 'storage_available': _storage_available(),
                    'warning': self._warning}

    def configure(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('Ожидается объект настроек OpenAI.')
        persist = payload.get('persist', False)
        if not isinstance(persist, bool):
            raise ValueError('Параметр сохранения должен быть true или false.')
        with self._lock:
            old_key, old_model = self._active()[0]
            supplied_key = payload.get('api_key', '')
            if not isinstance(supplied_key, str):
                raise ValueError('API-ключ должен быть строкой.')
            # Blank means retain a configured key; a malformed key is rejected.
            key = _key(supplied_key) if supplied_key else _key(old_key)
            model = _model(payload.get('model', old_model))
            if model == key:
                raise ValueError('Укажите ID модели в поле модели.')
            values = key, model
            if persist:
                if not _storage_available():
                    raise ValueError(_STORAGE_ERROR)
                self._save(values)
                # Commit memory only after atomic file replacement succeeds.
                self._saved, self._session, self._warning = values, None, ''
            else:
                self._session = values
            return self.public()

    def _save(self, values):
        temporary = self._path.with_name(self._path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            cleartext = json.dumps({'api_key': values[0], 'model': values[1]},
                                   ensure_ascii=True, separators=(',', ':')).encode('utf-8')
            encrypted = _encrypt(cleartext)
            envelope = json.dumps({'version': 1, 'protection': 'windows-dpapi-current-user',
                                   'ciphertext': base64.b64encode(encrypted).decode('ascii')}).encode('utf-8')
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Only ciphertext ever enters the temporary or final file.
            with temporary.open('xb') as handle:
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except Exception:
            raise ValueError('Не удалось сохранить защищённые настройки OpenAI. '
                             'Проверьте доступ к папке данных или используйте ключ только в сеансе.') from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
