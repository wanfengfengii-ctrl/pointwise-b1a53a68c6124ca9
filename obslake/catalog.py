import sqlite3
from pathlib import Path
from contextlib import contextmanager

class NotFound(LookupError):
    pass

class Unavailable(IOError):
    pass

class Catalog:
    def __init__(self, directory, hook=None):
        self.root = Path(directory).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.hook = hook
        self.db = sqlite3.connect(self.root / 'catalog.sqlite', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        try:
            self.initialize()
        except BaseException:
            self.db.close()
            raise

    def checkpoint(self, stage):
        if self.hook is not None:
            self.hook(stage)

    def initialize(self):
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise ValueError('unsupported catalog schema')
        if version == 1:
            return
        if self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
            raise ValueError('unversioned nonempty catalog')
        with self.transaction():
            self.db.execute('CREATE TABLE batches(id TEXT PRIMARY KEY, request TEXT UNIQUE NOT NULL, identity TEXT NOT NULL, row_count INTEGER NOT NULL, manifest TEXT NOT NULL)')
            self.db.execute('CREATE TABLE snapshots(id TEXT PRIMARY KEY, parent TEXT REFERENCES snapshots(id), body TEXT NOT NULL)')
            self.db.execute('CREATE TABLE current_head(singleton INTEGER PRIMARY KEY CHECK(singleton=1), snapshot_id TEXT NOT NULL REFERENCES snapshots(id))')
            self.db.execute('CREATE TABLE receipts(namespace TEXT NOT NULL, request TEXT NOT NULL, identity TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(namespace,request))')
            self.db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, definition TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','failed','complete')), result_id TEXT REFERENCES results(id), error TEXT)")
            self.db.execute("CREATE TABLE results(id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('delivery','reconciliation')), payload TEXT NOT NULL)")
            self.db.execute('CREATE TABLE offline(digest TEXT PRIMARY KEY)')
            self.db.execute("INSERT INTO snapshots VALUES('s-empty',NULL,?)", ('{"batches":[],"files":[],"id":"s-empty","parent":null}',))
            self.db.execute("INSERT INTO current_head VALUES(1,'s-empty')")
            self.db.execute('PRAGMA user_version=1')

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield self.db
            self.checkpoint('catalog.before_commit')
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def close(self):
        self.db.close()
