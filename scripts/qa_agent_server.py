"""Isolated browser QA only: synthetic records, fake provider, no external calls."""
import argparse
from contextlib import contextmanager
from datetime import date
import json
from pathlib import Path
import sys
import shutil
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import Application, Handler, ThreadingHTTPServer
from procurement.agent import AgentService


@contextmanager
def qa_directory():
    parent = (ROOT / 'outputs').resolve()
    parent.mkdir(exist_ok=True)
    folder = parent / ('agent-browser-qa-' + uuid.uuid4().hex)
    folder.mkdir()
    try:
        yield folder
    finally:
        if folder.resolve().parent != parent:
            raise RuntimeError('QA cleanup escaped outputs directory')
        shutil.rmtree(folder)


class TestSettings:
    """Never reads or writes real credentials; configure accepts only QA literal."""
    ready = False
    model = 'qa-fake-model'

    def public(self):
        return {'ready': self.ready, 'model': self.model, 'source': 'session',
                'persistent': False, 'storage_available': False, 'warning': ''}

    def credentials(self):
        return ('qa-not-a-real-key', self.model) if self.ready else ('', '')

    def configure(self, payload):
        if payload.get('api_key') not in ('qa-not-a-real-key', ''):
            raise ValueError('QA: используйте только фиктивный ключ qa-not-a-real-key')
        if payload.get('persist'):
            raise ValueError('QA: постоянное сохранение выключено')
        self.ready = True
        return self.public()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if args.port == 8765:
        parser.error('8765 reserved for the real application')
    with qa_directory() as folder:
        app = Application([], Path(folder), Path(folder) / 'historical')
        product = app.operations.save_entity('products', {'name': 'QA кабель', 'sku': 'QA-CABLE', 'unit': 'м', 'price': '12.50'})
        warehouse = app.operations.save_entity('warehouses', {'name': 'QA склад'})
        def transport(key, payload, timeout):
            inputs = payload['input']
            if inputs[-1].get('content') == 'Reply with OK.':
                return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK (QA)'}]}]}
            calls = [item for item in inputs if item.get('type') == 'function_call_output']
            if len(calls) == 0:
                name, arguments = 'search_products', {'query': 'QA-CABLE', 'limit': 5}
            elif len(calls) == 1:
                name, arguments = 'list_warehouses', {'query': 'QA', 'limit': 5}
            elif len(calls) == 2:
                name, arguments = 'prepare_document', {'kind': 'stock_in', 'date': date.today().isoformat(),
                    'warehouse_id': warehouse['id'], 'target_warehouse_id': '', 'counterparty_id': '',
                    'description': 'QA — проверка агента', 'lines': [{'product_id': product['id'], 'quantity': '10',
                    'price': '12.50', 'role': ''}], 'amount': None}
            else:
                return {'output': [{'type': 'message', 'content': [{'type': 'output_text',
                    'text': 'QA: подготовлено оприходование 10 м кабеля по 12.50. Проверьте карточку. Это тестовые данные и фиксированный ответ; OpenAI не вызывался.'}]}]}
            return {'output': [{'type': 'function_call', 'call_id': 'qa-' + str(len(calls)),
                                'name': name, 'arguments': json.dumps(arguments)}]}
        app.agent = AgentService(TestSettings(), app.agent.tools, transport)
        app.message = 'QA — синтетические данные, ответы без OpenAI'
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        server.application = app
        print(f'ISOLATED FAKE QA: http://127.0.0.1:{args.port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
