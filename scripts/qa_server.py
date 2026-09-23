"""Isolated synthetic UI test server; never opens or writes the company journal.

Run explicitly with Python; localhost:8766 contains QA products only.
"""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

import numpy as np
import pandas as pd
from app import Application, Handler, ThreadingHTTPServer
from procurement.data import Dataset
from procurement.engine import build_report, validate_settings


def synthetic_dataset():
    months=pd.date_range('2024-01-01',periods=32,freq='MS')
    definitions=[('QA-ORDER',30.,5.,0.,6.,'шт'),('QA-COVERED',30.,200.,0.,10.,'шт'),
        ('QA-LATE',30.,5.,0.,6.,'шт'),('QA-MISSING',30.,5.,0.,6.,'шт'),
        ('QA-UNKNOWN-STOCK',30.,None,None,6.,'шт'),('QA-FRACTION',.3,.05,0.,.1,'м'),
        ('QA-RESERVE',30.,1.,30.,6.,'шт'),('QA-RETURNS',30.,20.,0.,6.,'шт')]
    definitions += [(f'QA-PAGE-{n:03}',30.,200.,0.,10.,'шт') for n in range(65)]
    monthly,catalog,snapshots,constraints=[],[],[],[]
    for sku,quantity,stock,reserve,pack,unit in definitions:
        catalog.append({'supplier':'QA — СИНТЕТИЧЕСКИЕ ДАННЫЕ','sku':sku,'unit':unit,
                        'name':'Тестовый товар '+sku,'key':f'QA|{sku}|{unit}'})
        for month in months:
            value=quantity
            if sku=='QA-MISSING' and month==months[-1]: value=np.nan
            if sku=='QA-RETURNS' and month==months[-1]: value=-10.
            monthly.append({'supplier':catalog[-1]['supplier'],'sku':sku,'month':month,'quantity':value,'source':'QA synthetic monthly'})
        if stock is not None:
            snapshots.append({'supplier':catalog[-1]['supplier'],'sku':sku,'on_hand':stock,
                'reserved':reserve,'free_stock':stock-reserve,'snapshot_date':pd.Timestamp('2026-09-22'),
                'source':'QA synthetic snapshot'})
        constraints.append({'supplier':catalog[-1]['supplier'],'sku':sku,'value':pack,
                            'field':'Кратность','source':'QA synthetic constraint'})
    sales=pd.DataFrame({'supplier':pd.Series(dtype=str),'Код':pd.Series(dtype=str),
        'Ед.':pd.Series(dtype=str),'Количество':pd.Series(dtype=float),
        'date':pd.Series(dtype='datetime64[ns]'),'Номенклатура':pd.Series(dtype=str),'Документ':pd.Series(dtype=str)})
    shipments=pd.DataFrame([{'supplier':catalog[0]['supplier'],'sku':'QA-LATE','quantity':200.,
        'arrival':pd.Timestamp('2026-10-30'),'shipment':'QA delayed delivery','source':'QA synthetic inbound'}])
    dataset=Dataset(sales,pd.DataFrame(monthly),pd.DataFrame(),pd.DataFrame(catalog),shipments,
        pd.DataFrame(snapshots),pd.DataFrame(constraints),[],'qa-synthetic-v1',pd.Timestamp('2026-09-22'))
    lines=pd.DataFrame(columns=['supplier','sku','Ед.','pattern_class'])
    return dataset,lines


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    state=ROOT/'data'/'qa-site'
    app=Application([],state,state/'historical')
    app.data,app.lines=synthetic_dataset()
    settings=validate_settings({key:True for key in ('parameters_confirmed','constraints_confirmed',
        'incoming_confirmed','reserve_policy_confirmed')})
    # These confirmations belong only to the synthetic test scenario.
    app.report=build_report(app.data,app.lines,settings)
    app.report_revision=1
    app.message='QA: только синтетические тестовые данные'
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.application=app
    print(f'QA synthetic server: http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()


if __name__=='__main__':
    main()
