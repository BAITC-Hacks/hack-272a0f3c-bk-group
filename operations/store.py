"""Durable local operations. All quantities and money use integer ledger units.

Orders and invoices record intent only: they do not reserve or move stock.
Posting and cancellation are serialized SQLite transactions. No external services,
tax calculations, fiscal receipts or automatic opening balances are provided.
"""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
import sqlite3
import uuid


DOCUMENT_KINDS = (
    'purchase_order', 'purchase_invoice', 'receipt', 'supplier_return',
    'sales_order', 'sales_invoice', 'shipment', 'customer_return',
    'transfer', 'stock_in', 'write_off', 'inventory', 'payment_in',
    'payment_out', 'retail_sale', 'retail_return', 'production_order', 'production',
)
STOCK_IN = {'receipt', 'customer_return', 'stock_in', 'retail_return'}
STOCK_OUT = {'shipment', 'supplier_return', 'write_off', 'retail_sale'}
STOCK_KINDS = STOCK_IN | STOCK_OUT | {'transfer', 'inventory', 'production'}
PAYMENTS = {'payment_in', 'payment_out'}
PURCHASE = {'purchase_order', 'purchase_invoice', 'receipt', 'supplier_return'}
SALES = {'sales_order', 'sales_invoice', 'shipment', 'customer_return'}
PRODUCTION = {'production', 'production_order'}
QUANTITY_SCALE = 1_000_000
MONEY_SCALE = 100


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _text(value, label, required=False, limit=500):
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ValueError(f'{label}: требуется текст')
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise ValueError(f'{label}: укажите значение длиной до {limit} символов')
    return value


def _scaled(value, scale, label, *, zero=True, maximum='1000000000'):
    if isinstance(value, bool) or value is None:
        raise ValueError(f'{label}: укажите число')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal(maximum):
            raise ValueError()
        scaled = number * scale
        if scaled != scaled.to_integral_value() or (not zero and number == 0):
            raise ValueError()
        return int(scaled)
    except (InvalidOperation, ValueError, OverflowError):
        precision = 6 if scale == QUANTITY_SCALE else 2
        raise ValueError(f'{label}: требуется {"положительное" if not zero else "неотрицательное"} число, до {precision} знаков после запятой') from None


def _quantity(value):
    result = format(Decimal(value) / QUANTITY_SCALE, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def _money(value):
    return format(Decimal(value) / MONEY_SCALE, '.2f')


def _date(value, *, optional=False):
    if not value:
        return '' if optional else date.today().isoformat()
    if not isinstance(value, str):
        raise ValueError('Дата должна иметь формат ГГГГ-ММ-ДД')
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return value
    except ValueError:
        raise ValueError('Дата должна иметь формат ГГГГ-ММ-ДД') from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _entity_payload(value):
    """Expose a first revision for older records without rewriting their data."""
    record = json.loads(value)
    record.setdefault('version', 1)
    return record


class OperationsStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db(write=True) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS entities_kind ON entities(kind);
                CREATE TABLE IF NOT EXISTS product_keys (
                    external_key TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES entities(id)
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, number TEXT NOT NULL,
                    date TEXT NOT NULL, status TEXT NOT NULL,
                    version INTEGER NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(kind, number)
                );
                CREATE INDEX IF NOT EXISTS documents_kind ON documents(kind, date);
                CREATE TABLE IF NOT EXISTS stock_movements (
                    id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    warehouse_id TEXT NOT NULL REFERENCES entities(id),
                    product_id TEXT NOT NULL REFERENCES entities(id),
                    quantity INTEGER NOT NULL, reversal INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS stock_balance ON stock_movements(warehouse_id, product_id);
                CREATE TABLE IF NOT EXISTS cash_movements (
                    id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    amount INTEGER NOT NULL, reversal INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, action TEXT NOT NULL,
                    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, description TEXT NOT NULL
                );
            ''')

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _audit(self, db, action, kind, identifier, description):
        db.execute('INSERT INTO audit(created_at,action,entity_type,entity_id,description) VALUES (?,?,?,?,?)',
                   (_now(), action, kind, identifier, description))

    def _entities(self, db, kind):
        return [_entity_payload(row['payload']) for row in db.execute(
            'SELECT payload FROM entities WHERE kind=? ORDER BY rowid', (kind,))]

    def _entity(self, db, kind, identifier):
        row = db.execute('SELECT payload FROM entities WHERE id=? AND kind=?', (identifier, kind)).fetchone()
        if row is None:
            raise ValueError(f'Запись справочника {kind} не найдена')
        return _entity_payload(row['payload'])

    def _save_entity(self, db, kind, payload):
        if kind not in ('products', 'counterparties', 'warehouses', 'tasks'):
            raise ValueError('Неизвестный справочник')
        if not isinstance(payload, dict):
            raise ValueError('Некорректная запись справочника')
        identifier = _text(payload.get('id'), 'Идентификатор', limit=100)
        previous = self._entity(db, kind, identifier) if identifier else None
        if previous:
            version = payload.get('version')
            if type(version) is not int:
                raise ValueError('Для изменения записи требуется целая версия. Обновите страницу')
            if version != previous['version']:
                raise ValueError('Запись уже изменена. Обновите страницу перед сохранением')
        identifier = identifier or uuid.uuid4().hex
        now = _now()
        record = {'id': identifier, 'created_at': previous['created_at'] if previous else now,
                  'updated_at': now, 'version': previous['version'] + 1 if previous else 1}
        if kind == 'tasks':
            record.update(title=_text(payload.get('title'), 'Название задачи', True),
                          description=_text(payload.get('description'), 'Описание', limit=5000),
                          status=payload.get('status', 'open'), due_date=_date(payload.get('due_date'), optional=True))
            if record['status'] not in ('open', 'done'):
                raise ValueError('Неизвестный статус задачи')
        else:
            record['name'] = _text(payload.get('name'), 'Название', True)
        if kind == 'products':
            record.update(sku=_text(payload.get('sku'), 'Артикул', limit=150),
                          unit=_text(payload.get('unit'), 'Единица измерения', True, 40),
                          price=_money(_scaled(payload.get('price', '0'), MONEY_SCALE, 'Цена')),
                          external_keys=list((previous or {}).get('external_keys', [])))
            # A unit change would silently reinterpret historical ledger quantities.
            if previous and record['unit'] != previous['unit']:
                used = db.execute('SELECT 1 FROM stock_movements WHERE product_id=? LIMIT 1', (identifier,)).fetchone()
                if used:
                    raise ValueError('Нельзя менять единицу товара, по которому уже есть движения')
        if kind == 'counterparties':
            record['type'] = payload.get('type', 'both')
            if record['type'] not in ('customer', 'supplier', 'both'):
                raise ValueError('Неизвестный тип контрагента')
        db.execute('INSERT INTO entities(id,kind,payload) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                   (identifier, kind, _json(record)))
        self._audit(db, 'update' if previous else 'create', kind, identifier,
                    record.get('name', record.get('title', '')))
        return record

    def save_entity(self, kind, payload):
        with self._db(write=True) as db:
            return self._save_entity(db, kind, payload)

    def list_entities(self, kind):
        if kind not in ('products', 'counterparties', 'warehouses', 'tasks'):
            raise ValueError('Неизвестный справочник')
        with self._db() as db:
            return self._entities(db, kind)

    def list_tasks(self):
        with self._db() as db:
            return self._entities(db, 'tasks')

    def save_task(self, payload):
        return self.save_entity('tasks', payload)

    def _document(self, db, identifier):
        row = db.execute('SELECT payload FROM documents WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Документ не найден')
        return json.loads(row['payload'])

    def get_document(self, identifier):
        with self._db() as db:
            return self._document(db, identifier)

    def list_documents(self, kind=None):
        if kind and kind not in DOCUMENT_KINDS:
            raise ValueError('Неизвестный вид документа')
        with self._db() as db:
            rows = db.execute('SELECT payload FROM documents' + (' WHERE kind=?' if kind else '') + ' ORDER BY date DESC,rowid DESC',
                              (kind,) if kind else ())
            return [json.loads(row['payload']) for row in rows]

    def save_document(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('Некорректный документ')
        with self._db(write=True) as db:
            identifier = _text(payload.get('id'), 'Идентификатор', limit=100)
            previous = self._document(db, identifier) if identifier else None
            if previous and previous['status'] != 'draft':
                raise ValueError('Изменять можно только черновик')
            if previous:
                version = payload.get('version')
                if isinstance(version, bool) or not isinstance(version, int):
                    raise ValueError('Для изменения документа требуется целая версия. Обновите страницу')
                if version != previous['version']:
                    raise ValueError('Документ уже изменён. Обновите страницу')
            kind = payload.get('kind', (previous or {}).get('kind'))
            if kind not in DOCUMENT_KINDS or (previous and kind != previous['kind']):
                raise ValueError('Неизвестный или изменённый вид документа')
            identifier = identifier or uuid.uuid4().hex
            number = _text(payload.get('number'), 'Номер', limit=100)
            if not number:
                number = previous['number'] if previous else 'DOC-' + identifier[:10].upper()
            now = _now()
            record = {'id': identifier, 'kind': kind, 'number': number,
                      'date': _date(payload.get('date')), 'status': 'draft',
                      'version': previous['version'] + 1 if previous else 1,
                      'created_at': previous['created_at'] if previous else now, 'updated_at': now,
                      'description': _text(payload.get('description'), 'Описание', limit=5000)}
            for field, entity_kind in [('warehouse_id', 'warehouses'), ('target_warehouse_id', 'warehouses'),
                                       ('counterparty_id', 'counterparties')]:
                record[field] = _text(payload.get(field), field, limit=100)
                if record[field]:
                    self._entity(db, entity_kind, record[field])
            lines = payload.get('lines', [])
            if not isinstance(lines, list) or len(lines) > 500:
                raise ValueError('В документе допускается до 500 строк')
            if kind in PAYMENTS and lines:
                raise ValueError('Платёж не должен содержать товарные строки')
            record['lines'] = []
            seen = set()
            total = Decimal(0)
            for item in lines:
                if not isinstance(item, dict):
                    raise ValueError('Некорректная строка документа')
                product_id = _text(item.get('product_id'), 'Товар', True, 100)
                product = self._entity(db, 'products', product_id)
                quantity = _scaled(item.get('quantity'), QUANTITY_SCALE, 'Количество', zero=kind == 'inventory')
                price = _scaled(item.get('price', product['price']), MONEY_SCALE, 'Цена')
                role = item.get('role', 'material') if kind in PRODUCTION else ''
                if kind in PRODUCTION and role not in ('material', 'output'):
                    raise ValueError('Для производства укажите роль строки: material или output')
                if product_id in seen:
                    raise ValueError('Товар должен встречаться в документе только один раз')
                seen.add(product_id)
                record['lines'].append({'product_id': product_id, 'quantity': _quantity(quantity),
                                        'price': _money(price), 'role': role})
                if kind not in PRODUCTION or role == 'output':
                    total += (Decimal(quantity) * price / QUANTITY_SCALE).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            cents = _scaled(payload.get('amount', '0'), MONEY_SCALE, 'Сумма', maximum='1000000000000') if kind in PAYMENTS else int(total)
            if cents > 100_000_000_000_000:
                raise ValueError('Сумма документа слишком велика')
            record['amount'] = _money(cents)
            try:
                db.execute('''INSERT INTO documents(id,kind,number,date,status,version,payload) VALUES (?,?,?,?,?,?,?)
                              ON CONFLICT(id) DO UPDATE SET number=excluded.number,date=excluded.date,
                              version=excluded.version,payload=excluded.payload''',
                           (identifier, kind, number, record['date'], 'draft', record['version'], _json(record)))
            except sqlite3.IntegrityError:
                raise ValueError('Документ этого вида с таким номером уже существует') from None
            self._audit(db, 'update' if previous else 'create', 'documents', identifier, f'{kind} {number}')
            return record

    def _stock_balance(self, db, warehouse_id, product_id):
        return db.execute('SELECT COALESCE(SUM(quantity),0) FROM stock_movements WHERE warehouse_id=? AND product_id=?',
                          (warehouse_id, product_id)).fetchone()[0]

    def _cash_balance(self, db):
        return db.execute('SELECT COALESCE(SUM(amount),0) FROM cash_movements').fetchone()[0]

    def _posting_movements(self, db, doc):
        kind, warehouse = doc['kind'], doc['warehouse_id']
        if kind in STOCK_KINDS and not warehouse:
            raise ValueError('Укажите склад')
        if kind in PURCHASE | SALES:
            if not doc['counterparty_id']:
                raise ValueError('Укажите контрагента')
            partner = self._entity(db, 'counterparties', doc['counterparty_id'])
            expected = 'supplier' if kind in PURCHASE else 'customer'
            if partner['type'] not in (expected, 'both'):
                raise ValueError('Тип контрагента не подходит для этого документа')
        if kind not in PAYMENTS and not doc['lines']:
            raise ValueError('Добавьте хотя бы один товар')
        if kind in PRODUCTION:
            roles = {item['role'] for item in doc['lines']}
            if roles != {'material', 'output'}:
                raise ValueError('Укажите материалы и готовую продукцию')
        if kind == 'transfer' and (not doc['target_warehouse_id'] or doc['target_warehouse_id'] == warehouse):
            raise ValueError('Укажите другой склад назначения')
        movements = []
        for item in doc['lines']:
            product = item['product_id']
            quantity = _scaled(item['quantity'], QUANTITY_SCALE, 'Количество')
            if kind in STOCK_IN:
                movements.append((warehouse, product, quantity))
            elif kind in STOCK_OUT:
                movements.append((warehouse, product, -quantity))
            elif kind == 'transfer':
                movements.extend([(warehouse, product, -quantity), (doc['target_warehouse_id'], product, quantity)])
            elif kind == 'inventory':
                movements.append((warehouse, product, quantity - self._stock_balance(db, warehouse, product)))
            elif kind == 'production':
                movements.append((warehouse, product, quantity if item['role'] == 'output' else -quantity))
        cash = 0
        amount = _scaled(doc['amount'], MONEY_SCALE, 'Сумма', maximum='1000000000000')
        if kind in PAYMENTS and amount == 0:
            raise ValueError('Сумма платежа должна быть больше нуля')
        if kind in ('payment_in', 'retail_sale'):
            cash = amount
        elif kind in ('payment_out', 'retail_return'):
            cash = -amount
        return movements, cash

    def transition(self, identifier, action='post', expected_version=None):
        if action not in ('post', 'cancel'):
            raise ValueError('Неизвестное действие')
        with self._db(write=True) as db:
            doc = self._document(db, identifier)
            if expected_version is not None:
                if isinstance(expected_version, bool) or not isinstance(expected_version, int):
                    raise ValueError('Версия документа должна быть целым числом')
                if expected_version != doc['version']:
                    raise ValueError('Документ уже изменён. Обновите страницу перед проведением или отменой')
            if action == 'post':
                if doc['status'] != 'draft':
                    raise ValueError('Провести можно только черновик; документ уже обработан')
                if doc['kind'] in STOCK_KINDS | PAYMENTS and date.fromisoformat(doc['date']) > date.today():
                    raise ValueError('Нельзя проводить складскую или денежную операцию будущей датой. Оставьте документ черновиком до даты операции')
                movements, cash = self._posting_movements(db, doc)
                status, reversal = 'posted', 0
            else:
                if doc['status'] != 'posted':
                    raise ValueError('Отменить можно только проведённый документ')
                movements = [(row['warehouse_id'], row['product_id'], -row['quantity']) for row in db.execute(
                    'SELECT warehouse_id,product_id,quantity FROM stock_movements WHERE document_id=? AND reversal=0', (identifier,))]
                cash = -db.execute('SELECT COALESCE(SUM(amount),0) FROM cash_movements WHERE document_id=? AND reversal=0',
                                   (identifier,)).fetchone()[0]
                status, reversal = 'cancelled', 1
            deltas = {}
            for warehouse, product, quantity in movements:
                deltas[(warehouse, product)] = deltas.get((warehouse, product), 0) + quantity
            for (warehouse, product), delta in deltas.items():
                balance = self._stock_balance(db, warehouse, product)
                if balance + delta < 0:
                    name = self._entity(db, 'products', product)['name']
                    raise ValueError(f'Недостаточно остатка: {name}. Доступно {_quantity(balance)}')
                if balance + delta > 1_000_000_000 * QUANTITY_SCALE:
                    raise ValueError('Остаток превышает допустимое значение')
            new_cash_balance = self._cash_balance(db) + cash
            if new_cash_balance < 0:
                raise ValueError('Недостаточно средств в локальной кассе')
            if new_cash_balance > 100_000_000_000_000:
                raise ValueError('Денежный остаток превышает допустимое значение')
            now = _now()
            for (warehouse, product), quantity in deltas.items():
                if quantity:
                    db.execute('INSERT INTO stock_movements(document_id,warehouse_id,product_id,quantity,reversal,created_at) VALUES (?,?,?,?,?,?)',
                               (identifier, warehouse, product, quantity, reversal, now))
            if cash:
                db.execute('INSERT INTO cash_movements(document_id,amount,reversal,created_at) VALUES (?,?,?,?)',
                           (identifier, cash, reversal, now))
            doc.update(status=status, updated_at=now, version=doc['version'] + 1)
            db.execute('UPDATE documents SET status=?,version=?,payload=? WHERE id=?',
                       (status, doc['version'], _json(doc), identifier))
            self._audit(db, action, 'documents', identifier, f"{doc['kind']} {doc['number']}")
            return doc

    def _stock(self, db):
        entities = {r['id']: json.loads(r['payload']) for r in db.execute("SELECT id,payload FROM entities WHERE kind IN ('products','warehouses')")}
        return [{'product_id': r['product_id'], 'warehouse_id': r['warehouse_id'],
                 'product_name': entities[r['product_id']]['name'], 'warehouse_name': entities[r['warehouse_id']]['name'],
                 'sku': entities[r['product_id']]['sku'], 'unit': entities[r['product_id']]['unit'],
                 'quantity': _quantity(r['quantity'])}
                for r in db.execute('SELECT warehouse_id,product_id,SUM(quantity) quantity FROM stock_movements GROUP BY warehouse_id,product_id ORDER BY warehouse_id,product_id')]

    def stock(self):
        with self._db() as db:
            return self._stock(db)

    def _money(self, db):
        row = db.execute('''SELECT COALESCE(SUM(CASE WHEN c.amount>0 THEN c.amount ELSE 0 END),0) income,
                           COALESCE(SUM(CASE WHEN c.amount<0 THEN -c.amount ELSE 0 END),0) expense
                           FROM cash_movements c JOIN documents d ON d.id=c.document_id
                           WHERE c.reversal=0 AND d.status='posted' ''').fetchone()
        return {'balance': _money(self._cash_balance(db)), 'income': _money(row['income']), 'expense': _money(row['expense'])}

    def money(self):
        with self._db() as db:
            return self._money(db)

    def bootstrap(self):
        with self._db() as db:
            result = {kind: self._entities(db, kind) for kind in ('products', 'counterparties', 'warehouses', 'tasks')}
            statuses = dict(db.execute('SELECT status,COUNT(*) FROM documents GROUP BY status').fetchall())
            result['overview'] = {kind: len(result[kind]) for kind in ('products', 'counterparties', 'warehouses')}
            result['overview'].update(documents=sum(statuses.values()), draft_documents=statuses.get('draft', 0),
                                      posted_documents=statuses.get('posted', 0),
                                      tasks_open=sum(t['status'] == 'open' for t in result['tasks']))
            result['stock'], result['money'] = self._stock(db), self._money(db)
            return result

    def journal(self):
        with self._db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')]

    def overview(self):
        return self.bootstrap()['overview']

    def import_products(self, report_rows):
        """Explicit catalog import; stock is never inferred from forecast snapshots."""
        if not isinstance(report_rows, list):
            raise ValueError('Некорректный список товаров')
        result = {'created': 0, 'updated': 0, 'skipped': 0}
        with self._db(write=True) as db:
            for row in report_rows:
                if not isinstance(row, dict):
                    result['skipped'] += 1
                    continue
                key, name, unit = row.get('key'), row.get('name'), row.get('unit')
                if not all(isinstance(value, str) and value.strip() for value in (key, name, unit)) or len(name.strip()) > 500 or len(unit.strip()) > 40:
                    result['skipped'] += 1
                    continue
                sku = row.get('sku', '')
                if not isinstance(sku, str) or len(sku.strip()) > 150:
                    result['skipped'] += 1
                    continue
                existing = db.execute('SELECT product_id FROM product_keys WHERE external_key=?', (key,)).fetchone()
                if existing:
                    # Repeat import preserves user edits and the durable identifier.
                    result['skipped'] += 1
                    continue
                product = self._save_entity(db, 'products', {'name': name, 'sku': sku, 'unit': unit, 'price': '0'})
                # The external key is part of this new record's atomic creation,
                # not an edit of an already visible revision. Repeat imports
                # above do not change user edits or increment their version.
                product['external_keys'] = [key]
                db.execute('UPDATE entities SET payload=? WHERE id=?', (_json(product), product['id']))
                db.execute('INSERT INTO product_keys(external_key,product_id) VALUES (?,?)', (key, product['id']))
                result['created'] += 1
            self._audit(db, 'import', 'products', '', f"Импортировано карточек: {result['created']}; пропущено: {result['skipped']}. Остатки не изменены")
        return result
