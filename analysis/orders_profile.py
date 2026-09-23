import io
import json
import zipfile
from collections import Counter
from pathlib import Path

import pandas as pd

frames = []
for supplier, archive in [('IEK', 'IEK.zip'), ('Systeme Electric', 'Systeme electric.zip')]:
    with zipfile.ZipFile(Path('C:/Users/kaisar/Downloads') / archive) as z:
        name = next(n for n in z.namelist() if 'Динамика продаж' in n and n.endswith('.xlsx'))
        df = pd.read_excel(io.BytesIO(z.read(name)), dtype={'Код': str, 'Номер': str})
    df['supplier'] = supplier
    frames.append(df)
    print(json.dumps({'supplier': supplier, 'rows': len(df), 'columns': list(df.columns),
                      'units': df['Ед.'].value_counts(dropna=False).to_dict(),
                      'document_types': df['Документ'].astype(str).str.replace(r'\s+\S+\s+от\s+.*$', '', regex=True).value_counts().head(20).to_dict(),
                      'positive': int((df['Количество'] > 0).sum()),
                      'negative': int((df['Количество'] < 0).sum()),
                      'zero': int((df['Количество'] == 0).sum()),
                      'samples': df[['Дата','Номер','Документ','Ед.','Количество']].head(3).to_dict('records')}, ensure_ascii=False, default=str), flush=True)

all_data = pd.concat(frames, ignore_index=True)
all_data['date'] = pd.to_datetime(all_data['Дата'], dayfirst=True, errors='coerce')
print(json.dumps({'date_min': str(all_data.date.min()), 'date_max': str(all_data.date.max()),
                  'bad_dates': int(all_data.date.isna().sum()),
                  'by_year_sign_type': all_data.assign(year=all_data.date.dt.year, sign=all_data['Количество'].apply(lambda v: 'positive' if v > 0 else 'negative' if v < 0 else 'zero'), type=all_data['Документ'].astype(str).str.replace(r'\s+\S+\s+от\s+.*$', '', regex=True)).groupby(['year','type','sign']).size().to_string(),
                  'duplicate_rows_excluding_supplier': int(all_data.duplicated(subset=[c for c in all_data.columns if c != 'supplier']).sum())}, ensure_ascii=False), flush=True)
all_data.to_pickle(Path(__file__).with_name('orders_cache.pkl'))
