"""Explicit integration check on local archives, not part of fast unit tests.

The confirmed parameters below are test assumptions only, never saved to the UI.
"""
import json
import pickle
from pathlib import Path
import sys
import time
import zipfile
import io

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from procurement.engine import build_report, prepare_lines
from procurement.data import load_archives
from procurement.export import export_zip, tables
from app import default_paths, source_fingerprint, normalized_cache_path

root=Path(__file__).resolve().parents[1]
paths=default_paths()
cache=normalized_cache_path(root/'data', source_fingerprint(paths))
if cache.exists():
    with cache.open('rb') as handle:
        data,lines=pickle.load(handle)
else:
    data=load_archives(paths)
    lines=prepare_lines(data)
    with cache.open('wb') as handle:
        pickle.dump((data,lines),handle)
catalog_keys=set(zip(data.catalog.supplier,data.catalog.sku))
for frame in (data.monthly,data.balances,data.snapshots,data.constraints,data.shipments):
    assert set(zip(frame.supplier,frame.sku)) <= catalog_keys, 'Lost source SKU'
settings={key:True for key in ('parameters_confirmed','constraints_confirmed','incoming_confirmed','reserve_policy_confirmed')}
started=time.monotonic()
report=build_report(data,lines,settings)
ready=[row for row in report['rows'] if row['ready']]
assert ready, 'Expected some complete SKUs after test-only confirmations'
assert all(row['quantity'] is None for row in report['rows'] if not row['ready'])
assert all(abs(row['quantity']/row['pack_multiple']-round(row['quantity']/row['pack_multiple']))<1e-8 for row in ready)
assert all('customer_id' not in row for row in report['rows'])
sample=next(row for row in ready if row['quantity']>0)
assert sample['on_hand'] is not None and sample['reserved'] is not None
with zipfile.ZipFile(io.BytesIO(export_zip(report))) as archive:
    assert len(archive.namelist())>=3
output=root/'outputs'/'validation'
output.mkdir(parents=True,exist_ok=True)
(output/'tables.json').write_text(json.dumps(tables(report),ensure_ascii=False,allow_nan=False),encoding='utf-8')
(output/'results.json').write_text(json.dumps({'summary':report['summary'],'metrics':report['metrics'],
    'simulations':report['simulations'],'test_assumptions':settings,'elapsed_seconds':time.monotonic()-started,
    'sample':{k:sample[k] for k in ['supplier','sku','unit','on_hand','reserved','quantity','forecast_method','pack_multiple']}},ensure_ascii=False,indent=2),encoding='utf-8')
print((output/'results.json').read_text(encoding='utf-8'))
