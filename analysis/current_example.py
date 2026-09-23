import io,json,zipfile
from pathlib import Path
import pandas as pd
import openpyxl

codes={'030200192_','030200193_','030200201_'}
with zipfile.ZipFile('C:/Users/kaisar/Downloads/Systeme electric.zip') as z:
    for name in z.namelist():
        if not name.endswith('.xlsx') or 'Динамика' in name or 'Сезонность' in name: continue
        wb=openpyxl.load_workbook(io.BytesIO(z.read(name)),read_only=True,data_only=True)
        for s in wb.worksheets:
            rows=list(s.iter_rows(values_only=True))
            matched=[(i+1,row) for i,row in enumerate(rows) if any(str(v).strip() in codes for v in row)]
            if matched:
                print(json.dumps({'file':name,'sheet':s.title,'headers':rows[:3],'matched':matched},ensure_ascii=False,default=str),flush=True)
        wb.close()

d=pd.read_pickle(Path(__file__).with_name('orders_cache.pkl'))
d=d[d.supplier.eq('Systeme Electric') & d['Код'].isin(codes) & d['Документ'].astype(str).str.startswith('Расходная накладная ') & d.date.dt.year.isin([2025,2026]) & d['Количество'].notna()]
for code,h in d.groupby('Код'):
    results={}
    for year,y in h.groupby(h.date.dt.year):
        pos=y[y['Количество']>0]
        results[int(year)]={'rows':len(y),'positive_quantity':float(pos['Количество'].sum()),'negative_quantity':float(y.loc[y['Количество']<0,'Количество'].sum()),
            'monthly_net':y.groupby(y.date.dt.month)['Количество'].sum().to_dict(),
            'monthly_positive':pos.groupby(pos.date.dt.month)['Количество'].sum().to_dict(),
            'monthly_median':pos.groupby(pos.date.dt.month)['Количество'].median().to_dict(),
            'monthly_count':pos.groupby(pos.date.dt.month).size().to_dict(),
            'largest':pos.sort_values('Количество',ascending=False)[['date','Количество','Склад']].head(6).to_dict('records'),
            'last_date':str(y.date.max())}
    print(json.dumps({'sku':code,'sales':results},ensure_ascii=False,default=str),flush=True)
