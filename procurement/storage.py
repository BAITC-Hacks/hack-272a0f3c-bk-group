import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, created TEXT DEFAULT CURRENT_TIMESTAMP, action TEXT, payload TEXT)')

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key, default):
        with self.connect() as db:
            row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, key, value, action):
        self.put_many([(key, value, action)])

    def put_many(self, records):
        """Commit related state and audit entries together, or leave all unchanged."""
        records = [(key, json.dumps(value, ensure_ascii=False, allow_nan=False), action)
                   for key, value, action in records]
        with self.connect() as db:
            for key, payload, action in records:
                db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key,payload))
                db.execute('INSERT INTO journal(action,payload) VALUES (?,?)',(action,payload))

    def history(self):
        with self.connect() as db:
            return [dict(zip(('created','action','payload'),r)) for r in db.execute('SELECT created,action,payload FROM journal ORDER BY id DESC LIMIT 50')]
