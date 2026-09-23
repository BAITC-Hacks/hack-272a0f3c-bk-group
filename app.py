"""Run `python app.py`; local UI with optional cloud AI assistant."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import traceback
from urllib.parse import urlparse, parse_qs
import uuid
import hashlib
import pickle
import math
import csv
from datetime import date

import pandas as pd

from procurement.data import load_archives, Dataset
from procurement.engine import build_report, prepare_lines, validate_settings, DEFAULTS
from procurement.export import export_zip, export_xlsx
from procurement.storage import Store
from procurement.history import run_history, validate_period
from procurement.agent import AgentService
from procurement.agent_settings import OpenAISettings
from procurement.agent_tools import AgentTools
from operations import OperationsStore

ROOT=Path(__file__).resolve().parent


def pipeline_fingerprint(root=ROOT):
    """Invalidate every derived cache when a processing dependency changes."""
    digest = hashlib.sha256(b'procurement-pipeline-v3')
    paths = [root/'app.py', *sorted((root/'procurement').glob('*.py')),
             *sorted((root/'analysis').glob('*.py'))]
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def source_fingerprint(paths):
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def normalized_cache_path(state_dir, signature):
    return Path(state_dir)/f'normalized-{signature}-{pipeline_fingerprint()}.pkl'


def read_cache(path):
    """A interrupted private cache write must not make original files unusable."""
    try:
        with Path(path).open('rb') as handle:
            return pickle.load(handle)
    except (OSError, EOFError, pickle.UnpicklingError, ValueError, TypeError, AttributeError, ImportError):
        return None


def write_atomic(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def reject_json_constant(value):
    raise ValueError('Недопустимое числовое значение JSON')


def json_default(value):
    if isinstance(value,pd.Timestamp):
        return value.isoformat() if pd.notna(value) else None
    if hasattr(value,'item'):
        return value.item()
    raise TypeError(str(type(value)))


def encode(value):
    def clean(item):
        if item is pd.NaT:
            return None
        if isinstance(item,dict):
            return {key:clean(val) for key,val in item.items()}
        if isinstance(item,(list,tuple)):
            return [clean(val) for val in item]
        if isinstance(item,float) and not math.isfinite(item):
            return None
        if isinstance(item,pd.Timestamp):
            return item.isoformat()
        if hasattr(item,'item'):
            return clean(item.item())
        return item
    return json.dumps(clean(value),ensure_ascii=False,allow_nan=False).encode('utf-8')


def settings_for_dataset(store,fingerprint):
    settings=store.get('settings',DEFAULTS).copy()
    if store.get('settings_dataset',None)!=fingerprint:
        for key,value in DEFAULTS.items():
            if isinstance(value,bool):
                settings[key]=False
    return settings


class Application:
    def __init__(self,paths,state_dir,history_output_dir=None):
        self.paths=paths
        self.state_dir=Path(state_dir)
        self.history_output_dir=Path(history_output_dir) if history_output_dir is not None else ROOT/'outputs'/'historical'
        self.store=Store(self.state_dir/'journal.sqlite3')
        self.operations=OperationsStore(self.state_dir/'operations.sqlite3')
        self.data=None
        self.lines=None
        self.report=None
        self.history_report=None
        self.instance_id=uuid.uuid4().hex
        self.report_revision=0
        self.history_revision=0
        self.busy=False
        self.message='Ожидание загрузки'
        self.error=None
        self.lock=threading.Lock()
        self.agent=AgentService(OpenAISettings(self.state_dir),
                                AgentTools(self.operations,lambda:None if self.busy else self.report))

    def request_revision(self,request):
        if self.report is None:
            raise ValueError('Сначала загрузите данные')
        if ('expected_report_revision' not in request or 'expected_dataset' not in request
                or 'expected_instance_id' not in request
                or type(request['expected_report_revision']) is not int):
            raise ValueError('Расчёт изменился. Обновите данные и повторите действие.')
        return request['expected_report_revision'],request['expected_dataset'],request['expected_instance_id']

    def submit(self,operation,expected=None):
        with self.lock:
            if self.busy:
                raise ValueError('Расчет уже выполняется')
            if expected is not None and (self.data is None
                    or expected != (self.report_revision,self.data.fingerprint,self.instance_id)):
                raise ValueError('Расчёт изменился. Обновите данные и повторите действие.')
            self.busy=True
            self.error=None
            self.message='Выполняется операция…'
        def run():
            try:
                operation()
                self.message='Готово'
            except Exception as exc:
                self.error=str(exc)
                self.message='Расчет не завершен'
                traceback.print_exc()
            finally:
                with self.lock:
                    self.busy=False
        threading.Thread(target=run,daemon=True).start()

    def load(self,paths):
        signature=source_fingerprint(paths)
        cache=normalized_cache_path(self.state_dir,signature)
        # Only private cache files are unpickled, never uploaded archives.
        cached = read_cache(cache)
        if (isinstance(cached, tuple) and len(cached) == 2 and isinstance(cached[0], Dataset)
                and cached[0].fingerprint == signature and isinstance(cached[1], pd.DataFrame)):
            dataset,lines=cached
        else:
            self.message='Читаем продажи, остатки, ограничения и поставки из всех книг…'
            dataset=load_archives(paths)
            self.message='Подготавливаем историю продаж и объёмы партий…'
            lines=prepare_lines(dataset)
            write_atomic(cache,pickle.dumps((dataset,lines)))
        self.message='Рассчитываем прогноз, backtest и план закупок…'
        settings=settings_for_dataset(self.store,dataset.fingerprint)
        overrides=self.store.get('overrides',{})
        report_cache=self.report_cache_path(dataset,settings,overrides)
        report = read_cache(report_cache)
        if not (isinstance(report,dict) and report.get('dataset')==signature
                and isinstance(report.get('rows'),list) and 'settings' in report and 'metrics' in report):
            report=build_report(dataset,lines,settings,overrides,self.progress)
            write_atomic(report_cache,pickle.dumps(report))
        with self.lock:
            self.data,self.lines,self.report=dataset,lines,report
            self.history_report=None
            self.paths=paths
            self.report_revision+=1
            self.history_revision+=1

    def report_cache_path(self,dataset,settings,overrides):
        digest=hashlib.sha256(dataset.fingerprint.encode())
        digest.update(json.dumps([settings,overrides],sort_keys=True).encode())
        digest.update(pipeline_fingerprint().encode())
        return self.state_dir/('report-'+digest.hexdigest()+'.pkl')

    def calculate(self,settings,overrides=None):
        if self.data is None:
            raise ValueError('Сначала загрузите архивы')
        self.message='Пересчитываем рекомендации и историческую проверку…'
        report=build_report(self.data,self.lines,settings,overrides if overrides is not None else self.store.get('overrides',{}),self.progress)
        records=[('settings',settings,'Расчет с параметрами'),
                 ('settings_dataset',self.data.fingerprint,'Источник подтвержденных параметров')]
        if overrides is not None:
            records.append(('overrides',overrides,'Ручная корректировка входных данных'))
        self.store.put_many(records)
        with self.lock:
            self.report=report
            self.report_revision+=1

    def progress(self,message):
        self.message=message

    def evaluate_period(self,start,end):
        report, observations = run_history(self.data,start,end,self.progress)
        folder = self.history_output_dir
        folder.mkdir(parents=True,exist_ok=True)
        import io
        with io.StringIO(newline='') as handle:
            from procurement.export import safe_cell
            writer = csv.DictWriter(handle,fieldnames=list(observations[0]),delimiter=';')
            writer.writeheader()
            writer.writerows({key:safe_cell(value) for key,value in row.items()} for row in observations)
            write_atomic(folder/'forecasts.csv',handle.getvalue().encode('utf-8-sig'))
        write_atomic(folder/'report.json',encode(report))
        with self.lock:
            self.history_report=report
            self.history_revision+=1

    def upload(self, request):
        files=request.get('files')
        if not isinstance(files,list) or not 1<=len(files)<=2:
            raise ValueError('Загрузите один или два ZIP-архива')
        contents=[]
        for file in files:
            if not isinstance(file,dict) or not isinstance(file.get('content'),str):
                raise ValueError('Некорректное содержимое архива')
            try:
                raw=base64.b64decode(file['content'],validate=True)
            except (ValueError,TypeError):
                raise ValueError('Повреждённая передача архива: повторите загрузку') from None
            if not raw:
                raise ValueError('Пустой архив')
            contents.append(raw)
        def load_upload():
            folder=self.state_dir/'uploads'/uuid.uuid4().hex
            folder.mkdir(parents=True)
            paths=[]
            for index,content in enumerate(contents):
                # Client-supplied names never become filesystem paths.
                path=folder/f'archive-{index}.zip'
                write_atomic(path,content)
                paths.append(path)
            self.load(paths)
        self.submit(load_upload)

    def override(self,request,expected=None):
        if self.report is None:
            raise ValueError('Расчет еще не готов')
        key=request['key']
        if key not in {p['key'] for p in self.report['rows']}:
            raise ValueError('Неизвестный SKU')
        reason=request.get('reason','')
        if not isinstance(reason,str) or not 5<=len(reason.strip())<=2000:
            raise ValueError('Укажите источник или причину корректировки (от 5 до 2000 символов)')
        overrides=self.store.get('overrides',{})
        previous=overrides.get(key,{})
        record=previous.copy() if previous.get('dataset')==self.data.fingerprint else {}
        record.update(reason=reason.strip(),dataset=self.data.fingerprint)
        for field in ('on_hand','reserved','pack_multiple','minimum_order_quantity'):
            if field not in request:
                continue
            value=request.get(field)
            if value not in (None,''):
                if isinstance(value,bool):
                    raise ValueError('Некорректное значение '+field)
                try:
                    value=float(value)
                except (ValueError,TypeError):
                    raise ValueError('Некорректное значение '+field) from None
                if not 0<=value<=1e12 or (field=='pack_multiple' and value==0):
                    raise ValueError('Некорректное значение '+field)
                record[field]=value
            else:
                record.pop(field,None)
        if ('on_hand' in record) != ('reserved' in record):
            raise ValueError('Остаток и резерв указываются вместе; нулевой резерв вводится явно')
        if 'on_hand' in record:
            stamp=request.get('snapshot_date',record.get('snapshot_date'))
            try:
                if not isinstance(stamp,str) or date.fromisoformat(stamp).isoformat()!=stamp:
                    raise ValueError()
            except ValueError:
                raise ValueError('Дата среза должна иметь формат ГГГГ-ММ-ДД') from None
            record['snapshot_date']=stamp
        else:
            record.pop('snapshot_date',None)
        overrides[key]=record
        self.submit(lambda:self.calculate(self.report['settings'],overrides),expected=expected)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,format,*args):
        if args and str(args[1] if len(args)>1 else '').startswith('5'):
            super().log_message(format,*args)

    def send(self,data,kind='application/json; charset=utf-8',code=200,filename=None):
        try:
            self.send_response(code)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            if filename:
                self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            # Closing a tab cancels its request; never attempt a second response.
            self.close_connection=True

    def local_request(self):
        host=self.headers.get('Host','').split(':')[0]
        if host not in ('localhost','127.0.0.1'):
            raise ValueError('Разрешено только локальное подключение')
        origin=self.headers.get('Origin')
        if origin and urlparse(origin).netloc!=self.headers.get('Host'):
            raise ValueError('Запрос из другого источника запрещен')

    def do_GET(self):
        try:
            self.local_request()
            app=self.server.application
            url=urlparse(self.path)
            if url.path=='/':
                self.send((ROOT/'web/index.html').read_bytes(),'text/html; charset=utf-8')
            elif url.path in ('/assistant.js', '/assistant.css', '/operations.js', '/operations.css'):
                self.send((ROOT/'web'/url.path[1:]).read_bytes(),
                          'text/javascript; charset=utf-8' if url.path.endswith('.js') else 'text/css; charset=utf-8')
            elif url.path=='/api/assistant/config':
                self.send(encode(app.agent.configuration()))
            elif url.path.startswith('/api/operations/'):
                query=parse_qs(url.query)
                if url.path=='/api/operations/bootstrap':
                    result=app.operations.bootstrap()
                elif url.path=='/api/operations/documents':
                    result=app.operations.list_documents(query.get('kind',[None])[0])
                elif url.path=='/api/operations/document':
                    result=app.operations.get_document(query.get('id',[''])[0])
                elif url.path=='/api/operations/journal':
                    result=app.operations.journal()
                else:
                    raise ValueError('Раздел учёта не найден')
                self.send(encode(result))
            elif url.path=='/api/status':
                with app.lock:
                    status={'busy':app.busy,'message':app.message,'error':app.error,'loaded':app.report is not None,
                        'report_revision':app.report_revision,'history_revision':app.history_revision,'instance_id':app.instance_id}
                self.send(encode(status))
            elif url.path=='/api/report':
                with app.lock:
                    report,revision=app.report,app.report_revision
                if report is None:
                    raise ValueError('Данные еще не загружены')
                excluded={'history','examples','shipments','backtest','sources','override'}
                rows=[{k:v for k,v in r.items() if k not in excluded and k!='calculation'} |
                    {'order_date':(r['calculation'] or {}).get('order_date') if r['ready'] else None} for r in report['rows']]
                self.send(encode({**report,'rows':rows,'report_revision':revision,'instance_id':app.instance_id}))
            elif url.path=='/api/product':
                key=parse_qs(url.query).get('key',[''])[0]
                report=app.report
                if report is None:
                    raise ValueError('Данные еще не загружены')
                product=next((r for r in report['rows'] if r['key']==key),None)
                if product is None:
                    raise ValueError('SKU не найден')
                self.send(encode(product))
            elif url.path=='/api/journal':
                self.send(encode(app.store.history()))
            elif url.path=='/api/history':
                with app.lock:
                    dataset,result=app.data,app.history_report
                    revision,history_revision=app.report_revision,app.history_revision
                first=dataset.monthly.month.min() if dataset is not None and len(dataset.monthly) else None
                self.send(encode({'result':result,'report_revision':revision,'history_revision':history_revision,
                    'dataset':dataset.fingerprint if dataset is not None else None,'instance_id':app.instance_id,
                    'start':str((first+pd.DateOffset(months=6)).date())[:7] if first is not None else None,
                    'end':str((dataset.as_of.to_period('M').to_timestamp()-pd.DateOffset(months=1)).date())[:7] if dataset is not None else None}))
            elif url.path in ('/api/export.zip','/api/export.xlsx'):
                if app.report is None or app.busy:
                    raise ValueError('Дождитесь завершения расчета')
                if url.path.endswith('.zip'):
                    self.send(export_zip(app.report),'application/zip',filename='purchase-plan.zip')
                else:
                    self.send(export_xlsx(app.report),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename='purchase-plan.xlsx')
            else:
                self.send(encode({'error':'Не найдено'}),code=404)
        except Exception as exc:
            self.send(encode({'error':str(exc)}),code=400)

    def do_POST(self):
        try:
            self.local_request()
            length=int(self.headers.get('Content-Length','0'))
            if self.path=='/api/chat' and length>200_000:
                raise ValueError('Слишком длинный диалог')
            if self.path.startswith(('/api/agent/','/api/assistant/')) and length>50_000:
                raise ValueError('Запрос к агенту слишком большой')
            if self.path.startswith('/api/operations/') and length>2_000_000:
                raise ValueError('Документ слишком большой')
            if not 0<length<=50_000_000:
                raise ValueError('Запрос слишком большой или пустой')
            if self.headers.get_content_type()!='application/json':
                raise ValueError('Ожидается запрос application/json')
            try:
                request=json.loads(self.rfile.read(length),parse_constant=reject_json_constant)
            except (ValueError,UnicodeError):
                raise ValueError('Некорректный JSON в запросе') from None
            if not isinstance(request,dict):
                raise ValueError('Ожидается объект с параметрами запроса')
            if not isinstance(request,dict):
                raise ValueError('Ожидается объект запроса')
            app=self.server.application
            if self.path.startswith('/api/operations/'):
                if self.path=='/api/operations/entity':
                    result=app.operations.save_entity(request.get('kind'),request.get('payload'))
                elif self.path=='/api/operations/document':
                    result=app.operations.save_document(request)
                elif self.path=='/api/operations/transition':
                    version=request.get('expected_version')
                    if type(version) is not int or version<1:
                        raise ValueError('Обновите карточку документа перед проведением или отменой')
                    result=app.operations.transition(request.get('id'),request.get('action'),expected_version=version)
                elif self.path=='/api/operations/import-report':
                    if app.report is None or app.busy:
                        raise ValueError('Сначала загрузите данные и дождитесь расчёта плана закупок')
                    result=app.operations.import_products(app.report['rows'])
                else:
                    raise ValueError('Неизвестное действие учёта')
                self.send(encode(result))
                return
            elif self.path in ('/api/chat','/api/agent/chat'):
                self.send(encode(app.agent.chat(request)))
                return
            elif self.path=='/api/assistant/configure':
                self.send(encode(app.agent.configure(request)))
                return
            elif self.path=='/api/assistant/test':
                self.send(encode(app.agent.test_connection()))
                return
            elif self.path in ('/api/agent/confirm','/api/agent/decline'):
                self.send(encode(app.agent.act(request,self.path.rsplit('/',1)[-1])))
                return
            elif self.path=='/api/calculate':
                expected=app.request_revision(request)
                settings=validate_settings({key:value for key,value in request.items()
                    if key not in ('expected_report_revision','expected_dataset','expected_instance_id')})
                app.submit(lambda:app.calculate(settings),expected=expected)
            elif self.path=='/api/history-run':
                if app.data is None:
                    raise ValueError('Сначала загрузите данные')
                start,end=validate_period(request['start'],request['end'],app.data.as_of)
                app.submit(lambda:app.evaluate_period(start,end))
            elif self.path=='/api/override':
                app.override(request,expected=app.request_revision(request))
            elif self.path=='/api/load-default':
                paths=default_paths()
                if not paths:
                    raise ValueError('Архивы в Downloads не найдены. Загрузите их через интерфейс.')
                app.submit(lambda:app.load(paths))
            elif self.path=='/api/upload':
                app.upload(request)
            else:
                raise ValueError('Неизвестное действие')
            self.send(encode({'ok':True}),code=202)
        except Exception as exc:
            self.send(encode({'error':str(exc)}),code=400)


def default_paths():
    return [p for p in (Path.home()/'Downloads'/'IEK.zip',Path.home()/'Downloads'/'Systeme electric.zip') if p.exists()]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--archives',nargs='*')
    parser.add_argument('--no-auto-load',action='store_true',
                        help='Start without loading default archives from Downloads')
    parser.add_argument('--state-dir',default=str(ROOT/'data'))
    args=parser.parse_args()
    paths=[Path(p) for p in args.archives] if args.archives else ([] if args.no_auto_load else default_paths())
    application=Application(paths,args.state_dir)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.application=application
    if paths:
        application.submit(lambda:application.load(paths))
    print(f'Procurement MVP: http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()


if __name__=='__main__':
    main()
