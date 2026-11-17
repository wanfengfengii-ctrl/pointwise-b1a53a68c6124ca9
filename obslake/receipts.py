import json
from .records import validate_request_id
from .row_codec import canonical

class ReceiptStore:
    def __init__(self, catalog):
        self.catalog = catalog

    def lookup(self, namespace, request_id, identity):
        validate_request_id(request_id)
        row = self.catalog.db.execute('SELECT identity,response FROM receipts WHERE namespace=? AND request=?', (namespace, request_id)).fetchone()
        if row is None:
            return None
        if row['identity'] != identity:
            raise ValueError('request identity conflict')
        return json.loads(row['response'])

    def insert(self, namespace, request_id, identity, response):
        if not self.catalog.db.in_transaction:
            raise RuntimeError('receipt requires publication transaction')
        self.catalog.db.execute('INSERT INTO receipts VALUES(?,?,?,?)', (namespace, request_id, identity, canonical(response)))
        self.catalog.checkpoint('receipt.' + namespace)
