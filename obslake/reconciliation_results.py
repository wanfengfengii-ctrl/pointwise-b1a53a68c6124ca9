import hashlib
from .row_codec import canonical

class ReconciliationResults:
    def __init__(self, catalog, results, receipts):
        self.catalog, self.results, self.receipts = catalog, results, receipts

    def lookup_request(self, request_id, result_id):
        return self.receipts.lookup('reconcile', request_id, canonical([result_id]))

    def save(self, request_id, delivery_id, binding, accounting, discrepancies):
        identity = canonical([delivery_id])
        result_id = 'c-' + hashlib.sha256(canonical([request_id, delivery_id]).encode()).hexdigest()
        body = dict(delivery_id=delivery_id, binding=binding, accounting=accounting.as_dict(),
                    consistent=not discrepancies, discrepancies=[item.as_dict() for item in discrepancies])
        with self.catalog.transaction():
            replay = self.lookup_request(request_id, delivery_id)
            if replay is not None:
                return replay['result_id']
            self.results.publish_result(result_id, 'reconciliation', body)
            self.receipts.insert('reconcile', request_id, identity, dict(result_id=result_id))
            self.catalog.checkpoint('reconciliation.saved')
        return result_id

    def read(self, result_id):
        result = self.results.read_result(result_id)
        if result['kind'] != 'reconciliation':
            raise ValueError('not a reconciliation')
        return result
