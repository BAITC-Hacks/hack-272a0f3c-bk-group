"""Isolated HTTP, concurrency and persistence regressions. Never changes real data."""
import base64
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import pickle
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

import app
from procurement.engine import build_report
from procurement.storage import Store
from test_mvp import fixture

ROOT=Path(__file__).resolve().parents[1]


class ApplicationStressTests(unittest.TestCase):
    def setUp(self):
        folder=ROOT/'data'/'test-temp'
        folder.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=folder)
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.application=app.Application([],self.folder,self.folder/'history')
        data,lines=fixture()
        self.application.data,self.application.lines=data,lines
        self.application.report=build_report(data,lines)
        self.original=self.application.report

    @contextmanager
    def server(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        server.application=self.application
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def request(self,port,path,payload=None,headers=None,raw=None):
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        try:
            if isinstance(payload,dict) and 'expected_report_revision' in payload:
                payload={'expected_instance_id':self.application.instance_id,**payload}
            body=raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
            connection.request('POST' if body is not None else 'GET',path,body,
                               headers or ({'Content-Type':'application/json'} if body is not None else {}))
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:
            connection.close()

    def wait_job(self):
        end=time.monotonic()+5
        while self.application.busy and time.monotonic()<end:
            time.sleep(.005)
        self.assertFalse(self.application.busy,'Background operation did not finish')

    def test_http_rejects_wrong_payload_types_and_nonfinite_json(self):
        with self.server() as port:
            for body in (b'[]',b'null',b'"text"',b'{"lead_days":NaN}',b'{bad'):
                with self.subTest(body=body):
                    status,result=self.request(port,'/api/calculate',raw=body)
                    self.assertEqual(status,400)
                    self.assertIn('error',result)
            status,_=self.request(port,'/api/calculate',{},headers={'Content-Type':'text/plain'})
            self.assertEqual(status,400)
        self.assertIs(self.application.report,self.original)
        self.assertEqual(self.application.store.history(),[])

    def test_cross_origin_request_is_rejected_without_changes(self):
        with self.server() as port:
            status,_=self.request(port,'/api/calculate',{},headers={'Content-Type':'application/json','Origin':'https://other.example'})
            self.assertEqual(status,400)
        self.assertEqual(self.application.store.history(),[])

    def test_busy_upload_and_malformed_second_file_write_nothing(self):
        file={'content':base64.b64encode(b'fixture').decode()}
        self.application.busy=True
        with self.assertRaisesRegex(ValueError,'выполняется'):
            self.application.upload({'files':[file]})
        self.application.busy=False
        with self.assertRaises(ValueError):
            self.application.upload({'files':[file,{'content':'invalid base64'}]})
        self.assertFalse((self.folder/'uploads').exists())
        self.assertIs(self.application.report,self.original)

    def test_parallel_jobs_are_rejected_and_failed_job_can_recover(self):
        started,release=threading.Event(),threading.Event()
        def failing():
            started.set()
            release.wait(3)
            raise ValueError('Synthetic failure')
        with patch('app.traceback.print_exc'):
            self.application.submit(failing)
            self.assertTrue(started.wait(1))
            with self.assertRaisesRegex(ValueError,'выполняется'):
                self.application.submit(lambda:None)
            release.set()
            self.wait_job()
        self.assertEqual(self.application.error,'Synthetic failure')
        self.assertIs(self.application.report,self.original)
        self.application.submit(lambda:None)
        self.wait_job()
        self.assertIsNone(self.application.error)

    def test_persistence_failure_does_not_publish_new_report(self):
        with patch.object(self.application.store,'put_many',side_effect=sqlite3.OperationalError('simulated disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.application.calculate({'lead_days':10})
        self.assertIs(self.application.report,self.original)
        self.assertEqual(self.application.report_revision,0)
        self.assertEqual(self.application.store.history(),[])

    def test_settings_fingerprint_and_overrides_are_one_transaction(self):
        store=self.application.store
        with store.connect() as db:
            db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON state WHEN NEW.key='settings_dataset' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            store.put_many([('settings',{'lead_days':7},'settings'),('settings_dataset','fixture','source')])
        self.assertIsNone(store.get('settings',None))
        self.assertEqual(store.history(),[])

    def test_invalid_override_date_and_boolean_are_rejected_synchronously(self):
        base={'key':'S|sku|шт','reason':'Synthetic correction','on_hand':1,'reserved':0}
        for stamp in ('NaT','','2026-13-01','20260922',None):
            with self.subTest(stamp=stamp),self.assertRaisesRegex(ValueError,'Дата среза'):
                self.application.override({**base,'snapshot_date':stamp})
        with self.assertRaisesRegex(ValueError,'Некорректное значение'):
            self.application.override({**base,'on_hand':True,'snapshot_date':'2026-09-22'})
        self.assertFalse(self.application.busy)

    def test_partial_override_preserves_previous_confirmed_stock(self):
        key='S|sku|шт'
        self.application.store.put('overrides',{key:{'dataset':'fixture','reason':'Old synthetic reason',
            'on_hand':12.,'reserved':2.,'snapshot_date':'2026-09-22','pack_multiple':6.}},'test')
        with patch.object(self.application,'submit',side_effect=lambda operation,**kwargs:operation()):
            self.application.override({'key':key,'reason':'Updated packaging only','pack_multiple':12})
        override=self.application.store.get('overrides',{})[key]
        self.assertEqual((override['on_hand'],override['reserved'],override['pack_multiple']),(12.,2.,12.))
        self.assertEqual(self.application.report_revision,1)

    def test_corrupt_caches_rebuild_from_original_inputs(self):
        data,lines=fixture()
        data.fingerprint='fixed-source'
        cache=self.folder/'normalized.pkl'
        cache.write_bytes(b'\x80\x04truncated')
        report_cache=self.folder/'report.pkl'
        report_cache.write_bytes(pickle.dumps({'broken':True}))
        with patch('app.source_fingerprint',return_value='fixed-source'),patch('app.normalized_cache_path',return_value=cache),\
             patch('app.load_archives',return_value=data) as loader,patch('app.prepare_lines',return_value=lines),\
             patch.object(self.application,'report_cache_path',return_value=report_cache):
            self.application.load(['synthetic-placeholder'])
        loader.assert_called_once()
        self.assertEqual(self.application.report['dataset'],'fixed-source')
        self.assertEqual(self.application.report_revision,1)
        self.assertEqual(self.application.history_revision,1)
        self.assertEqual(app.read_cache(report_cache)['dataset'],'fixed-source')
        self.assertFalse(list(self.folder.glob('*.tmp')))

    def test_revision_api_and_successful_recalculation(self):
        with self.server() as port:
            status,value=self.request(port,'/api/calculate',{'lead_days':5,
                'expected_report_revision':0,'expected_dataset':'fixture'})
            self.assertEqual(status,202,value)
            self.wait_job()
            _,state=self.request(port,'/api/status')
            _,report=self.request(port,'/api/report')
            _,history=self.request(port,'/api/history')
        self.assertEqual(state['report_revision'],1)
        self.assertEqual(report['report_revision'],1)
        self.assertEqual(history['dataset'],'fixture')
        self.assertEqual(report['settings']['lead_days'],5)

    def test_stale_tab_cannot_overwrite_current_parameters_or_stock(self):
        old={'expected_report_revision':0,'expected_dataset':'fixture'}
        with self.server() as port:
            self.assertEqual(self.request(port,'/api/calculate',{'lead_days':5,**old})[0],202)
            self.wait_job()
            for path,values in [('/api/calculate',{'lead_days':90}),
                ('/api/override',{'key':'S|sku|шт','on_hand':999,'reserved':0,
                    'snapshot_date':'2026-09-22','reason':'Stale synthetic correction'})]:
                status,result=self.request(port,path,{**values,**old})
                self.assertEqual(status,400)
                self.assertIn('изменился',result['error'])
        self.assertEqual(self.application.report['settings']['lead_days'],5)
        self.assertEqual(self.application.store.get('overrides',{}),{})

    def test_product_before_loading_has_readable_error(self):
        self.application.report=None
        with self.server() as port:
            status,result=self.request(port,'/api/product?key=unknown')
        self.assertEqual(status,400)
        self.assertIn('не загружены',result['error'])

    def test_old_server_instance_is_rejected_even_when_revision_matches(self):
        with self.server() as port:
            status,result=self.request(port,'/api/calculate',{'lead_days':5,
                'expected_report_revision':0,'expected_dataset':'fixture','expected_instance_id':'before-restart'})
        self.assertEqual(status,400)
        self.assertIn('изменился',result['error'])
        self.assertIs(self.application.report,self.original)

    def test_closed_browser_connection_does_not_attempt_error_response(self):
        handler=app.Handler.__new__(app.Handler)
        handler.send_response=Mock()
        handler.send_header=Mock()
        handler.end_headers=Mock()
        handler.wfile=Mock()
        handler.wfile.write.side_effect=ConnectionAbortedError('closed QA tab')
        handler.send(b'{}')
        self.assertTrue(handler.close_connection)
        handler.wfile.write.assert_called_once()


if __name__=='__main__':
    unittest.main()
