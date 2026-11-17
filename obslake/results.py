import json
from .catalog import NotFound
from .row_codec import canonical

class Results:
    def __init__(self, catalog):
        self.catalog = catalog

    def publish_result(self, result_id, kind, payload):
        if not self.catalog.db.in_transaction:
            raise RuntimeError('result requires transaction')
        if kind not in ('delivery', 'reconciliation'):
            raise ValueError('unknown result kind')
        self.catalog.db.execute('INSERT INTO results VALUES(?,?,?)', (result_id, kind, canonical(payload)))
        self.catalog.checkpoint('result.insert')

    def read_result(self, result_id):
        if not isinstance(result_id, str):
            raise NotFound(result_id)
        row = self.catalog.db.execute('SELECT kind,payload FROM results WHERE id=?', (result_id,)).fetchone()
        if row is None:
            raise NotFound(result_id)
        return dict(result_id=result_id, kind=row['kind'], payload=json.loads(row['payload']))
