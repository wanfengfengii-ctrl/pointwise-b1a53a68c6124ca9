from .records import validate_request_id
from .source_accounting import account_sources
from .discrepancies import compare_accounting

class Reconciler:
    def __init__(self, snapshots, results, objects, reconciliation_results):
        self.snapshots, self.results = snapshots, results
        self.objects, self.reconciliation_results = objects, reconciliation_results

    def reconcile(self, request_id, result_id):
        validate_request_id(request_id)
        replay = self.reconciliation_results.lookup_request(request_id, result_id)
        if replay is not None:
            return replay['result_id']
        result = self.results.read_result(result_id)
        if result['kind'] != 'delivery':
            raise ValueError('reconcile requires a delivery')
        delivery = result['payload']
        binding = delivery['binding']
        snapshot = self.snapshots.snapshot(binding['snapshot_id'])
        accounting = account_sources(snapshot, binding, self.objects)
        differences = compare_accounting(accounting, delivery)
        return self.reconciliation_results.save(request_id, result_id, binding, accounting, differences)
