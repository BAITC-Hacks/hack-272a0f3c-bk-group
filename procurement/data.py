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
MAX_ARCHIVE_BYTES = 200_000_000
MAX_EXPANDED_BYTES = 600_000_000
MAX_ARCHIVE_ENTRIES = 200
MAX_WORKBOOK_ENTRIES = 10_000


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
    if isinstance(value, str):
        value = value.strip().replace('\u00a0', '').replace(' ', '').replace(',', '.')
    value = pd.to_numeric(value, errors='coerce')
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def source_number(value, source, field, nonnegative=False):
    """Unknown cells stay unknown; malformed populated cells cannot become zero."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, str) and value.strip() in ('', '-', '—'):
        return None
    parsed = number(value)
    if isinstance(value, (bool, np.bool_)) or parsed is None or (nonnegative and parsed < 0):
        rule = 'неотрицательное конечное число' if nonnegative else 'конечное число'
        raise ValueError(f'{source}: колонка «{field}» должна содержать {rule}; получено {value!r}')
    return parsed


def validate_zip(archive, label, max_entries):
    entries = archive.infolist()
    if len(entries) > max_entries or sum(i.file_size for i in entries) > MAX_EXPANDED_BYTES:
        raise ValueError(f'{label}: слишком много файлов или слишком большой размер после распаковки')
    names = [i.filename.replace('\\', '/').casefold() for i in entries]
    if len(set(names)) != len(names):
        raise ValueError(f'{label}: повторяющиеся имена файлов в архиве')
    if any(i.flag_bits & 1 for i in entries):
        raise ValueError(f'{label}: зашифрованные архивы не поддерживаются')
    return sum(i.file_size for i in entries)


def workbook_kind(name):
    lower = name.lower()
    for text, kind in (('динамика продаж', 'sales'), ('ежемесячные продажи', 'monthly'),
                       ('ежемесячные остатки', 'balances'), ('moq', 'constraints'),
                       ('путь иэк', 'incoming'), ('товар в пути', 'snapshot')):
        if text in lower:
            return kind
    return None


def source_supplier(name, archive_name):
    """Known adapters recognize explicit labels; unknown sources keep their own label."""
    text = name.casefold()
    matches = []
    if re.search(r'(?<![a-z])(?:system(?:e)?(?:\s*electric)?|syseme\s+electric)(?![a-z])', text):
        matches.append('Systeme Electric')
    if re.search(r'(?<![a-zа-я])(?:iek|иэк)(?![a-zа-я])', text):
        matches.append('IEK')
    if len(matches) > 1:
        raise ValueError(f'{name}: неоднозначное название поставщика')
    if matches:
        return matches[0]
    return source_supplier(archive_name, '') if archive_name else Path(name).stem


def require_columns(frame, columns, source):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f'{source}: отсутствуют обязательные колонки: {", ".join(sorted(missing))}')


def shipment_date(label, snapshot_date):
    match = re.search(r'в пути (\d{2})\.(\d{2})(?:\.(\d{4}))?', str(label), flags=re.I)
    if not match:
        return pd.NaT
    if match[3]:
        return pd.to_datetime(f'{match[3]}-{match[2]}-{match[1]}', errors='coerce')
    if pd.isna(snapshot_date):
        return pd.NaT
    arrival = pd.to_datetime(f'{snapshot_date.year}-{match[2]}-{match[1]}', errors='coerce')
    # A January column in a December snapshot could mean an overdue receipt or
    # next January. Neither year can be proved from a day/month-only label.
    return arrival if pd.notna(arrival) and arrival >= snapshot_date else pd.NaT


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
                    'key': '|'.join(str(value).replace('%', '%25').replace('|', '%7C')
                                    for value in (supplier, sku, unit)),
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
    seen_archives, seen_workbooks = set(), set()
    expanded_workbooks = 0
    for path in paths:
        path = Path(path)
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError(f'{path.name}: архив превышает допустимый размер')
        content = path.read_bytes()
        digest.update(content)
        archive_hash = hashlib.sha256(content).digest()
        if archive_hash in seen_archives:
            raise ValueError(f'{path.name}: один и тот же архив загружен несколько раз')
        seen_archives.add(archive_hash)
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise ValueError(f'{path.name}: файл не является исправным ZIP-архивом')
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            validate_zip(archive, path.name, MAX_ARCHIVE_ENTRIES)
            supported_books = 0
            for name in archive.namelist():
                kind = workbook_kind(name)
                if not name.lower().endswith('.xlsx') or kind is None:
                    continue
                supplier = source_supplier(name, path.stem)
                raw = archive.read(name)
                source = f'{path.name} / {name}'
                if not zipfile.is_zipfile(io.BytesIO(raw)):
                    raise ValueError(f'{source}: файл не является исправной книгой XLSX')
                with zipfile.ZipFile(io.BytesIO(raw)) as xlsx:
                    expanded_workbooks += validate_zip(xlsx, source, MAX_WORKBOOK_ENTRIES)
                    if expanded_workbooks > MAX_EXPANDED_BYTES:
                        raise ValueError('Суммарный размер содержимого книг после распаковки слишком большой')
                workbook_hash = hashlib.sha256(raw).digest()
                if workbook_hash in seen_workbooks:
                    raise ValueError(f'{source}: одна и та же книга загружена несколько раз')
                seen_workbooks.add(workbook_hash)
                book = pd.ExcelFile(io.BytesIO(raw))
                supported_books += 1
                sources.append({'supplier': supplier, 'file': name, 'sheets': book.sheet_names})
                date_match = re.search(r'(\d{2}\.\d{2}\.20\d{2})', name)
                snapshot_date = pd.to_datetime(date_match.group(), dayfirst=True, errors='coerce') if date_match else pd.NaT
                if date_match and pd.isna(snapshot_date):
                    raise ValueError(f'{source}: недопустимая дата в названии файла')
                if pd.notna(snapshot_date) and kind in ('incoming', 'snapshot'):
                    dates.append(snapshot_date)
                if kind == 'sales':
                    df = book.parse(dtype={'Код': str, 'Номер': str})
                    require_columns(df, ('Дата', 'Код', 'Количество', 'Документ'), source)
                    df['supplier'] = supplier
                    df['date'] = pd.to_datetime(df['Дата'], dayfirst=True, errors='coerce', format='mixed')
                    df['Код'] = df['Код'].map(clean_code)
                    df['Количество'] = df['Количество'].map(lambda value: source_number(value, source, 'Количество'))
                    for target, aliases in (('Ед.', ('Ед.', 'Ед.изм', 'Ед. изм.', 'Ед. изм')),
                                            ('Номенклатура', ('Номенклатура', 'Наименование'))):
                        values = pd.Series('', index=df.index, dtype=str)
                        for column in aliases:
                            if column in df:
                                values = values.mask(values.eq(''), df[column].map(clean_code))
                        df[target] = values
                    df['source'] = source
                    sales.append(df)
                elif kind in ('monthly', 'balances'):
                    df = book.parse(dtype={'Номенклатура.Код': str})
                    require_columns(df, ('Номенклатура.Код',), source)
                    if not any(month_date(column) is not None for column in df.columns):
                        raise ValueError(f'{source}: не найдены колонки месяцев с годом')
                    for _, row in df.iterrows():
                        code = clean_code(row.get('Номенклатура.Код'))
                        if not code or code.lower() == 'итого':
                            continue
                        for column in df.columns:
                            period = month_date(column)
                            if period is not None:
                                record = {'supplier': supplier, 'sku': code, 'month': period,
                                          'quantity': source_number(row[column], source, column), 'source': source,
                                          **product_metadata(row)}
                                (balances if kind == 'balances' else monthly).append(record)
                elif kind == 'constraints':
                    df = book.parse(dtype={'Код 1с': str, 'Номенклатура.Код': str})
                    code_field = 'Код 1с' if 'Код 1с' in df else 'Номенклатура.Код'
                    field = next((c for c in ('Мин. разр. к отгр.', 'Кратность') if c in df), None)
                    require_columns(df, (code_field,), source)
                    if field is None:
                        raise ValueError(f'{source}: не найдена колонка кратности или минимальной партии')
                    for _, row in df.iterrows():
                        code = clean_code(row.get('Код 1с', row.get('Номенклатура.Код')))
                        if code:
                            constraints.append({'supplier': supplier, 'sku': code,
                                                'value': source_number(row.get(field), source, field, nonnegative=True), 'field': field, 'source': source,
                                                **product_metadata(row)})
                elif kind == 'incoming':
                    df = book.parse(dtype={'Код 1с': str})
                    require_columns(df, ('Код 1с',), source)
                    for _, row in df.iterrows():
                        code = clean_code(row.get('Код 1с'))
                        if not code:
                            continue
                        for column in df.columns[3:]:
                            match = re.search(r'поступление до (\d{2}\.\d{2}\.\d{4})', str(column))
                            qty = source_number(row[column], source, column, nonnegative=True)
                            if qty is not None and qty > 0:
                                shipments.append({'supplier': supplier, 'sku': code, 'quantity': qty,
                                    'arrival': pd.to_datetime(match.group(1), dayfirst=True, errors='coerce') if match else pd.NaT,
                                    'source': source, 'shipment': str(column), 'snapshot_date': snapshot_date,
                                    **product_metadata(row)})
                elif kind == 'snapshot':
                    df = book.parse(header=1, dtype={'Код 1с': str})
                    require_columns(df, ('Код 1с', 'Остаток', 'Зарезервировано'), source)
                    for _, row in df.iterrows():
                        code = clean_code(row.get('Код 1с'))
                        if not code:
                            continue
                        snapshots.append({'supplier': supplier, 'sku': code,
                            'on_hand': source_number(row.get('Остаток'), source, 'Остаток'),
                            'reserved': source_number(row.get('Зарезервировано'), source, 'Зарезервировано', nonnegative=True),
                            'free_stock': source_number(row.get('Свободный остаток'), source, 'Свободный остаток'), 'snapshot_date': snapshot_date,
                            'source': source, **product_metadata(row)})
                        for column in df.columns:
                            match = re.search(r'в пути (\d{2})\.(\d{2})', str(column))
                            qty = source_number(row[column], source, column, nonnegative=True) if match else None
                            if qty is not None and qty > 0:
                                arrival = shipment_date(column, snapshot_date)
                                shipments.append({'supplier': supplier, 'sku': code, 'quantity': qty,
                                    'arrival': arrival, 'source': source, 'shipment': str(column), 'snapshot_date': snapshot_date,
                                    **product_metadata(row)})
                book.close()
            if not supported_books:
                raise ValueError(f'{path.name}: не найдены поддерживаемые книги XLSX. Проверьте названия и структуру выгрузки.')
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
