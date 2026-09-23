"""Read the supplied workbook layouts without inventing missing values."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import io
import re
import zipfile

import numpy as np
import pandas as pd


MONTHS = {'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'май': 5, 'июн': 6,
          'июл': 7, 'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12}


def month_date(value):
    text = str(value).strip().lower()
    year = re.search(r'20\d{2}', text)
    if not year:
        return None
    for prefix, month in MONTHS.items():
        if text.startswith(prefix):
            return pd.Timestamp(int(year.group()), month, 1)
    return None


def clean_code(value):
    return '' if pd.isna(value) else str(value).strip()


def number(value):
    value = pd.to_numeric(value, errors='coerce')
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def product_metadata(row):
    """Read explicit labels only; do not infer units from a product name."""
    fields = {str(key).strip(): value for key, value in row.items()}
    def first(names):
        return next((clean_code(fields[n]) for n in names
                     if n in fields and clean_code(fields[n])), '')
    return {'name': first(('Номенклатура', 'Наименование')),
            'unit': first(('Ед.', 'Ед.изм', 'Ед. изм.', 'Ед. изм'))}


def build_catalog(sales, *sources):
    """Union all source keys, preserving unknown and conflicting units."""
    records = []
    for row in sales.to_dict('records'):
        records.append({'supplier': row['supplier'], 'sku': clean_code(row['Код']),
                        **product_metadata(row), 'source': row.get('source', '')})
    for frame in sources:
        for row in frame.to_dict('records'):
            records.append({'supplier': row['supplier'], 'sku': clean_code(row['sku']),
                'name': clean_code(row.get('name')), 'unit': clean_code(row.get('unit')),
                'source': row.get('source', '')})
    products = []
    candidates = pd.DataFrame(records).drop_duplicates()
    if len(candidates):
        for (supplier, sku), group in candidates[candidates.sku.ne('')].groupby(['supplier','sku'], sort=True):
            units = sorted(set(group.unit) - {''}) or ['']
            names = group.loc[group.name.ne(''), 'name']
            for unit in units:
                products.append({'supplier': supplier, 'sku': sku, 'unit': unit,
                    'name': names.iloc[0] if len(names) else sku,
                    'key': f'{supplier}|{sku}|{unit}',
                    'catalog_sources': sorted(set(group.source) - {''})})
    return pd.DataFrame(products, columns=['supplier','sku','unit','name','key','catalog_sources'])


@dataclass
class Dataset:
    sales: pd.DataFrame
    monthly: pd.DataFrame
    balances: pd.DataFrame
    catalog: pd.DataFrame
    shipments: pd.DataFrame
    snapshots: pd.DataFrame
    constraints: pd.DataFrame
    sources: list
    fingerprint: str
    as_of: pd.Timestamp


def load_archives(paths):
    sales, monthly, balances, constraints, snapshots, shipments, sources = ([] for _ in range(7))
    digest = hashlib.sha256()
    dates = []
    for path in paths:
        path = Path(path)
        content = path.read_bytes()
        digest.update(content)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 600_000_000:
                raise ValueError('Архив слишком большой после распаковки')
            supplier = 'Systeme Electric' if any('system' in n.lower() for n in archive.namelist()) else 'IEK'
            for name in archive.namelist():
                if not name.lower().endswith('.xlsx'):
                    continue
                raw = archive.read(name)
                book = pd.ExcelFile(io.BytesIO(raw))
                source = f'{path.name} / {name}'
                sources.append({'supplier': supplier, 'file': name, 'sheets': book.sheet_names})
                date_match = re.search(r'(\d{2}\.\d{2}\.20\d{2})', name)
                snapshot_date = pd.to_datetime(date_match.group(), dayfirst=True) if date_match else pd.NaT
                if pd.notna(snapshot_date):
                    dates.append(snapshot_date)
                if 'Динамика продаж' in name:
                    df = book.parse(dtype={'Код': str, 'Номер': str})
                    df['supplier'] = supplier
                    df['date'] = pd.to_datetime(df['Дата'], dayfirst=True, errors='coerce')
                    df['Код'] = df['Код'].map(clean_code)
                    df['Количество'] = pd.to_numeric(df['Количество'], errors='coerce')
                    df['source'] = source
                    sales.append(df)
                elif 'Ежемесячные продажи' in name or 'Ежемесячные остатки' in name:
                    df = book.parse(dtype={'Номенклатура.Код': str})
                    for _, row in df.iterrows():
                        code = clean_code(row.get('Номенклатура.Код'))
                        if not code or code.lower() == 'итого':
                            continue
                        for column in df.columns:
                            period = month_date(column)
                            if period is not None:
                                record = {'supplier': supplier, 'sku': code, 'month': period,
                                          'quantity': number(row[column]), 'source': source,
                                          **product_metadata(row)}
                                (balances if 'остатки' in name else monthly).append(record)
                elif 'MOQ' in name:
                    df = book.parse()
                    for _, row in df.iterrows():
                        code = clean_code(row.get('Код 1с', row.get('Номенклатура.Код')))
                        if code:
                            field = 'Мин. разр. к отгр.' if supplier == 'IEK' else 'Кратность'
                            constraints.append({'supplier': supplier, 'sku': code,
                                                'value': number(row.get(field)), 'field': field, 'source': source,
                                                **product_metadata(row)})
                elif 'Путь ИЭК' in name:
                    df = book.parse(dtype={'Код 1с': str})
                    for _, row in df.iterrows():
                        code = clean_code(row.get('Код 1с'))
                        if not code:
                            continue
                        for column in df.columns[3:]:
                            match = re.search(r'поступление до (\d{2}\.\d{2}\.\d{4})', str(column))
                            qty = number(row[column])
                            if qty is not None and qty > 0:
                                shipments.append({'supplier': supplier, 'sku': code, 'quantity': qty,
                                    'arrival': pd.to_datetime(match.group(1), dayfirst=True) if match else pd.NaT,
                                    'source': source, 'shipment': str(column), 'snapshot_date': snapshot_date,
                                    **product_metadata(row)})
                elif 'Товар в пути' in name:
                    df = book.parse(header=1, dtype={'Код 1с': str})
                    for _, row in df.iterrows():
                        code = clean_code(row.get('Код 1с'))
                        if not code:
                            continue
                        snapshots.append({'supplier': supplier, 'sku': code,
                            'on_hand': number(row.get('Остаток')), 'reserved': number(row.get('Зарезервировано')),
                            'free_stock': number(row.get('Свободный остаток')), 'snapshot_date': snapshot_date,
                            'source': source, **product_metadata(row)})
                        for column in df.columns:
                            match = re.search(r'в пути (\d{2})\.(\d{2})', str(column))
                            qty = number(row[column]) if match else None
                            if qty is not None and qty > 0:
                                arrival = pd.Timestamp(snapshot_date.year, int(match[2]), int(match[1])) if pd.notna(snapshot_date) else pd.NaT
                                shipments.append({'supplier': supplier, 'sku': code, 'quantity': qty,
                                    'arrival': arrival, 'source': source, 'shipment': str(column), 'snapshot_date': snapshot_date,
                                    **product_metadata(row)})
                book.close()
    sales = pd.concat(sales, ignore_index=True) if sales else pd.DataFrame(columns=[
        'supplier','Код','Ед.','Номенклатура','Документ','Количество','source','date'])
    sales['date'] = pd.to_datetime(sales['date'])
    sales['Ед.'] = sales['Ед.'].map(clean_code)
    def frame(rows, columns):
        return pd.DataFrame(rows, columns=columns + ['name','unit'])
    last_sale = sales['date'].max()
    as_of = max(dates) if dates else (last_sale.normalize() if pd.notna(last_sale) else pd.NaT)
    if pd.isna(as_of):
        raise ValueError('Не удалось определить дату данных. Добавьте датированный складской срез или историю продаж.')
    data = Dataset(sales, frame(monthly, ['supplier','sku','month','quantity','source']),
        frame(balances, ['supplier','sku','month','quantity','source']), pd.DataFrame(),
        frame(shipments, ['supplier','sku','quantity','arrival','source','shipment','snapshot_date']),
        frame(snapshots, ['supplier','sku','on_hand','reserved','free_stock','snapshot_date','source']),
        frame(constraints, ['supplier','sku','value','field','source']), sources, digest.hexdigest(), as_of)
    data.catalog = build_catalog(data.sales, data.monthly, data.balances, data.snapshots, data.constraints, data.shipments)
    if data.catalog.empty:
        raise ValueError('Товары не найдены. Проверьте структуру книг и колонки кодов товаров.')
    return data
