"""Exports are snapshots of a calculation, not executable orders."""
import csv
import io
import json
import hashlib
import math
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def safe_cell(value):
    if isinstance(value, str) and (value.startswith(('\t', '\r', '\n')) or value.lstrip().startswith(('=', '+', '-', '@'))):
        return "'" + value
    return value


def supplier_filename(supplier, used):
    """Write flat, portable names even when a source label contains a path."""
    stem = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', str(supplier)).strip(' .')[:100] or 'Поставщик'
    if stem.upper().split('.')[0] in {'CON','PRN','AUX','NUL', *(f'{p}{i}' for p in ('COM','LPT') for i in range(1,10))}:
        stem = '_' + stem
    candidate = f'{stem} - проект заказа.csv'
    if candidate.casefold() in used:
        suffix = hashlib.sha256(str(supplier).encode()).hexdigest()[:10]
        candidate = f'{stem}-{suffix} - проект заказа.csv'
    used.add(candidate.casefold())
    return candidate


def tables(report):
    headers = ['Поставщик','SKU','Товар','Ед.','Статус','Количество к проверке','Спрос на горизонт',
        'Остаток','Резерв','В пути на горизонт','Страховой запас','Кратность','Дата заказа','Приход заказа','Причины / ограничения']
    rows=[]
    for row in report['rows']:
        calc=row.get('calculation') or {}
        rows.append([row['supplier'],row['sku'],row['name'],row['unit'],
            {'blocked':'Недостаточно данных','order':'К закупке','urgent':'Риск раннего дефицита','covered':'Запас покрывает горизонт'}[row['status']],
            row['quantity'],row['forecast_horizon'],row['on_hand'],row['reserved'],calc.get('incoming_in_horizon'),
            row['safety_stock'],row['pack_multiple'],calc.get('order_date') if row['ready'] else None,
            calc.get('arrival_date') if row['ready'] else None,'; '.join(row['blocks']+row['warnings'])])
    settings=[['Дата среза',report['as_of']],['Дата расчета',report['created_at']],['Отпечаток данных',report['dataset']]]
    labels={'lead_days':'Срок поставки, дней','review_days':'Период пересмотра, дней','service':'Квантиль страхового запаса',
        'scenario':'Сценарий','blank_months_zero':'Пустые месяцы = 0 (допущение)', 'max_snapshot_age':'Допустимый возраст среза',
        'parameters_confirmed':'Параметры подтверждены закупщиком','constraints_confirmed':'Кратность и единицы подтверждены',
        'incoming_confirmed':'Поставки и их единицы подтверждены','reserve_policy_confirmed':'Резерв сверх прогноза подтвержден'}
    settings += [[labels.get(k,k),v] for k,v in report['settings'].items()]
    settings += [['Ограничение',v] for v in report['limitations']]
    settings += [['Источник',s['file']] for s in report['sources']]
    return {'План закупки':[headers]+rows,'Параметры и источники':[['Параметр','Значение']]+settings}


def csv_bytes(rows):
    text=io.StringIO(newline='')
    writer=csv.writer(text,delimiter=';')
    writer.writerows([[safe_cell(v) for v in row] for row in rows])
    return text.getvalue().encode('utf-8-sig')


def export_zip(report):
    all_tables=tables(report)
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('План со статусами.csv',csv_bytes(all_tables['План закупки']))
        archive.writestr('Параметры.csv',csv_bytes(all_tables['Параметры и источники']))
        used_names = {name.casefold() for name in archive.namelist()}
        paired = list(zip(report['rows'], all_tables['План закупки'][1:]))
        for supplier in sorted({r['supplier'] for r in report['rows']}):
            ready=[cells for row,cells in paired if row['supplier']==supplier
                and row.get('ready') is True and row.get('status') != 'blocked'
                and not row.get('blocks') and isinstance(row.get('quantity'), (int,float))
                and math.isfinite(row['quantity']) and row['quantity']>0]
            archive.writestr(supplier_filename(supplier, used_names),csv_bytes([all_tables['План закупки'][0]]+ready))
    return buffer.getvalue()


def export_xlsx(report):
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    node=str(runtime) if runtime.exists() else 'node'
    temp_root=ROOT/'data'/'exports'
    temp_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='procurement-',dir=temp_root) as folder:
        source=Path(folder)/'tables.json'
        target=Path(folder)/'plan.xlsx'
        source.write_text(json.dumps(tables(report),ensure_ascii=False,allow_nan=False),encoding='utf-8')
        result=subprocess.run([node,str(ROOT/'scripts/export_xlsx.mjs'),str(source),str(target)],
            capture_output=True,text=True,timeout=120)
        if result.returncode:
            raise RuntimeError('XLSX export: '+result.stderr[-500:])
        return target.read_bytes()
