"""Exports are snapshots of a calculation, not executable orders."""
import csv
import io
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


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
        for supplier in sorted({r['supplier'] for r in report['rows']}):
            ready=[row for row in all_tables['План закупки'][1:] if row[0]==supplier and row[5] is not None and row[5]>0]
            archive.writestr(f'{supplier} - проект заказа.csv',csv_bytes([all_tables['План закупки'][0]]+ready))
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
