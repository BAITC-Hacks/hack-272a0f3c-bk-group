"""Synthetic format and hostile-input fixtures. No company/customer data."""
import csv
import io
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

from analysis.orders_analysis import build_invoice_lines, classify_invoice_line_patterns, summarize
from procurement.data import (build_catalog, load_archives, source_number, source_supplier,
                              shipment_date)
from procurement.export import export_xlsx, export_zip, safe_cell


def xlsx_payload(rows):
    """Minimal OOXML protocol fixture, intentionally without workbook authoring dependencies."""
    body = []
    for row_number, row in enumerate(rows, 1):
        cells = []
        for column, value in enumerate(row, 1):
            letters, n = '', column
            while n:
                n, digit = divmod(n - 1, 26)
                letters = chr(65 + digit) + letters
            ref = f'{letters}{row_number}'
            if value is None:
                continue
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>')
        body.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    contents = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="QA" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(body) + '</sheetData></worksheet>',
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            archive.writestr(name, content)
    return output.getvalue()


def fixture_books(label='QA-A', shipment=0, unit='шт', sku='0007'):
    months = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек']
    labels = [f'{m} {year}' for year in (2024, 2025) for m in months]
    labels += [f'{m} 2026' for m in months[:9]]
    return {
        f'{label}/Ежемесячные продажи.xlsx': xlsx_payload([
            ['Номенклатура.Код', 'Номенклатура', 'Ед.', *labels],
            [sku, f'Синтетический товар {label}', unit, *([30] * len(labels))]]),
        f'{label}/MOQ.xlsx': xlsx_payload([
            ['Код 1с','Номенклатура','Ед.','Кратность'],
            [sku, f'Синтетический товар {label}', unit, 6]]),
        f'{label}/Товар в пути 22.09.2026.xlsx': xlsx_payload([
            [f'Синтетический склад {label}'],
            ['Код 1с','Номенклатура','Ед.','Остаток','Зарезервировано','Свободный остаток','в пути 25.09'],
            [sku, f'Синтетический товар {label}', unit, 10, 0, 10, shipment]]),
    }


def write_fixture_archive(folder, label='QA-A', **kwargs):
    """Return an isolated upload ZIP path, with complete monthly history and snapshot."""
    path = Path(folder) / f'{label}.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in fixture_books(label, **kwargs).items():
            archive.writestr(name, content)
    return path


class ImportStressTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / 'data' / 'test-temp'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.folder.cleanup)

    def write_archive(self, books, name='QA.zip'):
        path = Path(self.folder.name) / name
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for label, content in books.items():
                archive.writestr(label, content)
        return path

    def test_unknown_supplier_is_not_silently_relabelled_and_leading_zero_survives(self):
        path = write_fixture_archive(self.folder.name)
        data = load_archives([path])
        self.assertEqual(data.catalog.supplier.tolist(), ['QA-A'])
        self.assertEqual(data.catalog.sku.tolist(), ['0007'])
        self.assertEqual(data.constraints.sku.tolist(), ['0007'])
        self.assertEqual(data.as_of, pd.Timestamp('2026-09-22'))
        # The UI pipeline must also accept an archive without invoice detail.
        from procurement.engine import prepare_lines
        self.assertTrue(prepare_lines(data).empty)

    def test_mixed_supplier_archive_resolves_each_workbook(self):
        books = {**fixture_books('IEK'), **fixture_books('Systeme Electric')}
        data = load_archives([self.write_archive(books)])
        self.assertEqual(set(data.catalog.supplier), {'IEK','Systeme Electric'})
        with self.assertRaisesRegex(ValueError, 'неоднозначное'):
            source_supplier('IEK/Systeme Electric.xlsx', 'mixed')
        self.assertEqual(source_supplier('Динамика продаж.xlsx', 'Ecosystem'), 'Ecosystem')

    def test_delimiters_in_source_codes_cannot_alias_another_product(self):
        products = pd.DataFrame([
            {'supplier':'A|B','sku':'C','unit':'шт','name':'one'},
            {'supplier':'A','sku':'B|C','unit':'шт','name':'two'},
            {'supplier':'A','sku':'B%7CC','unit':'шт','name':'three'}])
        catalog = build_catalog(pd.DataFrame(), products)
        self.assertEqual(catalog.key.nunique(), 3)

    def test_missing_unit_survives_as_unknown(self):
        data = load_archives([write_fixture_archive(self.folder.name, unit='')])
        self.assertEqual(data.catalog.unit.tolist(), [''])

    def test_duplicate_upload_is_not_double_counted(self):
        path = write_fixture_archive(self.folder.name)
        with self.assertRaisesRegex(ValueError, 'архив.*несколько раз'):
            load_archives([path, path])

    def test_duplicate_workbook_is_not_double_counted(self):
        books = fixture_books()
        original = next(iter(books.values()))
        books['QA-A/Ежемесячные продажи копия.xlsx'] = original
        with self.assertRaisesRegex(ValueError, 'книга.*несколько раз'):
            load_archives([self.write_archive(books)])

    def test_unrecognized_or_broken_archive_is_clear_error(self):
        with self.assertRaisesRegex(ValueError, 'поддерживаемые книги'):
            load_archives([self.write_archive({'unknown.xlsx': b'ignored'})])
        broken = Path(self.folder.name) / 'broken.zip'
        broken.write_bytes(b'not a zip')
        with self.assertRaisesRegex(ValueError, 'ZIP'):
            load_archives([broken])

    def test_duplicate_archive_names_rejected(self):
        path = Path(self.folder.name) / 'duplicate.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('book.xlsx', b'a')
                archive.writestr('book.xlsx', b'b')
        with self.assertRaisesRegex(ValueError, 'повторяющиеся имена'):
            load_archives([path])

    def test_nested_xlsx_expansion_limit_checked_before_pandas(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('xl/worksheets/sheet1.xml', 'x' * 100_000)
        path = self.write_archive({'Ежемесячные продажи.xlsx': inner.getvalue()})
        with patch('procurement.data.MAX_EXPANDED_BYTES', 50_000):
            with self.assertRaisesRegex(ValueError, 'после распаковки'):
                load_archives([path])

    def test_missing_required_columns_do_not_silently_load_partial_data(self):
        books = fixture_books()
        books['QA-A/Динамика продаж.xlsx'] = xlsx_payload([['Код','Дата'],['0007','2026-09-20']])
        with self.assertRaisesRegex(ValueError, 'обязательные колонки'):
            load_archives([self.write_archive(books)])

    def test_invalid_incoming_is_not_silently_treated_as_zero(self):
        for value in ('inf', 'not-a-quantity', -7):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'неотрицательное конечное число'):
                    load_archives([write_fixture_archive(self.folder.name, shipment=value)])

    def test_localized_quantity_and_unknown_are_distinct(self):
        self.assertEqual(source_number('1\u00a0234,5','test','qty'), 1234.5)
        self.assertIsNone(source_number('', 'test', 'qty'))
        self.assertEqual(source_number(0, 'test', 'qty'), 0)
        for value in ('NaN', float('inf'), True):
            with self.assertRaises(ValueError):
                source_number(value, 'test', 'qty')

    def test_new_year_shipment_needs_explicit_year(self):
        snapshot = pd.Timestamp('2026-12-22')
        self.assertTrue(pd.isna(shipment_date('в пути 05.01', snapshot)))
        self.assertEqual(shipment_date('в пути 05.01.2027', snapshot), pd.Timestamp('2027-01-05'))
        self.assertTrue(pd.isna(shipment_date('в пути 31.02.2027', snapshot)))


class InvoiceStressTests(unittest.TestCase):
    def rows(self):
        return pd.DataFrame([{'supplier':'QA','Код':'001','Ед.':'шт','Номенклатура':'Synthetic',
            'Документ':'Расходная накладная 1','date':pd.Timestamp(date),'Количество':qty}
            for date,qty in [('2025-01-01',10), ('2025-02-01',20), ('2026-01-01',30)]])

    def test_reused_document_numbers_on_different_dates_are_separate_purchases(self):
        _, lines = build_invoice_lines(self.rows())
        self.assertEqual(lines.qty.tolist(), [10,20,30])
        self.assertEqual(summarize(classify_invoice_line_patterns(lines))['invoices'], 3)

    def test_infinity_and_blank_codes_cannot_distort_invoice_statistics(self):
        rows = self.rows()
        rows['Количество'] = rows['Количество'].astype(float)
        rows.loc[0,'Количество'] = np.inf
        rows.loc[1,'Код'] = '  '
        _, lines = build_invoice_lines(rows)
        self.assertEqual(lines.qty.tolist(), [30])

    def test_fractional_moq_does_not_add_extra_pack(self):
        _, lines = build_invoice_lines(self.rows())
        result = classify_invoice_line_patterns(lines, {('QA','001','шт'):
            {'pack_multiple':.1, 'minimum_order_quantity':.1+.2}})
        self.assertAlmostEqual(result.batch_floor.iloc[0], .3)


class ExportStressTests(unittest.TestCase):
    def report(self):
        row = {'supplier':'../../CON','sku':'0007','name':'=HYPERLINK("bad")','unit':'шт',
            'status':'order','ready':True,'quantity':12,'forecast_horizon':10,'on_hand':0,
            'reserved':0,'safety_stock':2,'pack_multiple':6,'blocks':[],'warnings':[],'calculation':{}}
        blocked = dict(row, sku='blocked', ready=False, status='blocked', quantity=99, blocks=['missing'])
        return {'rows':[row, blocked, dict(row,supplier='..\\..\\CON')], 'as_of':'2026-09-22',
                'created_at':'2026-09-22', 'dataset':'synthetic', 'settings':{}, 'limitations':[], 'sources':[]}

    def test_exports_have_safe_unique_flat_names_and_no_blocked_orders(self):
        with zipfile.ZipFile(io.BytesIO(export_zip(self.report()))) as archive:
            self.assertEqual(len(archive.namelist()), len(set(n.casefold() for n in archive.namelist())))
            for name in archive.namelist():
                self.assertEqual(len(PurePosixPath(name).parts), 1)
                self.assertNotIn('\\', name)
                if 'проект заказа' in name:
                    rows = list(csv.reader(io.StringIO(archive.read(name).decode('utf-8-sig')),delimiter=';'))
                    self.assertEqual(len(rows),2)
                    self.assertEqual(rows[1][1], '0007')
                    self.assertTrue(rows[1][2].startswith("'="))

    def test_control_prefixed_csv_values_are_literals(self):
        for value in ('\t=1+1','\n=1','  +1','@SUM(A1)','-cmd'):
            self.assertTrue(safe_cell(value).startswith("'"))
        self.assertEqual(safe_cell(-12), -12)

    def test_xlsx_small_nonzero_quantities_keep_numeric_values_and_visible_precision(self):
        from openpyxl import load_workbook
        report = self.report()
        template = report['rows'][0]
        report['rows'] = [dict(template, quantity=value, pack_multiple=value,
            on_hand=-value, sku=f'QA-{i}') for i,value in enumerate((.0004, .001, .0000004))]
        workbook = load_workbook(io.BytesIO(export_xlsx(report)), data_only=False)
        sheet = workbook['План закупки']
        for row_number, value in enumerate((.0004, .001, .0000004), 6):
            for column in ('F','L'):
                cell = sheet[f'{column}{row_number}']
                self.assertEqual(cell.value, value)
                self.assertEqual(cell.data_type, 'n')
                self.assertIn('######', cell.number_format)
                self.assertEqual('E+' in cell.number_format, value < 1e-6)
            self.assertEqual(sheet[f'H{row_number}'].value, -value)
            self.assertIn('[Red]', sheet[f'H{row_number}'].number_format)
        self.assertEqual(sheet['C6'].data_type, 's')
        self.assertEqual(sheet['C6'].value.lstrip("'"), template['name'])
        workbook.close()


if __name__ == '__main__':
    unittest.main()
