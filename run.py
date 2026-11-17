import argparse
import json
import sys
import unittest
from pathlib import Path
from obslake.catalog import Catalog, NotFound, Unavailable
from obslake.snapshots import SnapshotStore
from obslake.receipts import ReceiptStore
from obslake.object_store import ObjectStore
from obslake.intake import Intake
from obslake.results import Results
from obslake.query_jobs import QueryJobs
from obslake.export import Exporter
from obslake.reconciliation_results import ReconciliationResults
from obslake.reconcile import Reconciler
from obslake.specs import SpecRegistry

class Lake:
    def __init__(self, directory, hook=None):
        self.catalog = Catalog(directory, hook)
        self.snapshots = SnapshotStore(self.catalog)
        self.receipts = ReceiptStore(self.catalog)
        self.objects = ObjectStore(self.catalog)
        self.specs = SpecRegistry(self.catalog)
        self.intake = Intake(self.catalog, self.snapshots, self.receipts, self.objects, self.specs)
        self.results = Results(self.catalog)
        self.queries = QueryJobs(self.catalog, self.snapshots, self.receipts, self.objects, self.results)
        self.exporter = Exporter(self.catalog, self.results, self.receipts)
        self.reconciliations = ReconciliationResults(self.catalog, self.results, self.receipts)
        self.reconciler = Reconciler(self.snapshots, self.results, self.objects, self.reconciliations)
        self.write = self.intake.write
        self.snapshot = self.snapshots.snapshot
        self.create_query = self.queries.create_query
        self.run_query = self.queries.run_query
        self.read_result = self.results.read_result
        self.export_result = self.exporter.export_result
        self.reconcile = self.reconciler.reconcile
        self.register_spec = self.specs.register
        self.set_write_spec = self.specs.set_write_spec

    def close(self):
        self.catalog.close()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()

def mixed_demo(directory):
    """Mixed V1 + spec files, a cold partition, recovery and reopen."""
    from obslake.catalog import Unavailable
    spec = [{"column": "time", "transform": "floor", "width": 10},
            {"column": "time", "transform": "mod", "modulus": 4}]
    with Lake(directory) as lake:
        lake.write('mixed-legacy', [dict(id='l1', time=-1, device=7, amount=2)])
        spec_id = lake.register_spec(spec)
        lake.set_write_spec(spec_id)
        receipt = lake.write('mixed-spec', [
            dict(id='m1', time=3, device=7, amount=5),
            dict(id='m3', time=None, device=7, amount=11),
            dict(id='m4', time=10, device=7, amount=None)])
        snapshot = lake.snapshot(receipt['snapshot_id'])
        cold = next(item for item in snapshot['files']
                    if item.get('spec_id') == spec_id and item['partition'] == [0, 3])
        lake.objects.set_available(cold['digest'], False)
        excluded = lake.create_query('mixed-excluded', snapshot['id'], 0, 1,
                                     ['and', ['ge', 'time', 8], ['lt', 'time', 10]])
        lake.objects.read_trace.clear()
        excluded_result = lake.read_result(lake.run_query(excluded))
        excluded_reads = list(lake.objects.read_trace)
        possible = lake.create_query('mixed-possible', snapshot['id'], 0, 1,
                                     ['and', ['ge', 'time', 7], ['lt', 'time', 10]])
        if lake.queries.read_job(possible)['status'] == 'complete':
            offline_outcome = 'replayed-complete'
        else:
            try:
                lake.run_query(possible)
                offline_outcome = 'completed'
            except Unavailable:
                offline_outcome = 'Unavailable'
    with Lake(directory) as lake:
        lake.objects.set_available(cold['digest'], True)
        recovered = lake.read_result(lake.run_query(possible))
        mixed = lake.create_query('mixed-accounting', snapshot['id'], -1, 1,
                                  ['lt', 'time', 9])
        result = lake.read_result(lake.run_query(mixed))
        audit = lake.read_result(lake.reconcile('mixed-audit', result['result_id']))['payload']
    with Lake(directory) as lake:
        reopened = lake.read_result(result['result_id'])
        replayed_audit = lake.read_result(lake.reconcile('mixed-audit', result['result_id']))['payload']
    expected_rows = ['l1', 'm1']
    expected_groups = [dict(device=7, row_count=2, amount_count=2, sum_amount=7)]
    expected_reasons = dict(null_time=1, outside_day=0, predicate_false=1,
                            predicate_unknown=0, selected=2)
    actual_rows = [row['id'] for row in result['payload']['rows']]
    if excluded_reads or excluded_result['payload']['rows']:
        raise AssertionError('excluded cold partition was read or returned rows')
    if offline_outcome not in ('Unavailable', 'replayed-complete') or recovered['payload']['rows']:
        raise AssertionError('necessary cold partition did not fail then recover empty')
    if actual_rows != expected_rows or result['payload']['groups'] != expected_groups:
        raise AssertionError('mixed delivery differs from hand calculation')
    if (audit['accounting']['source_count'] != 4
            or audit['accounting']['reasons'] != expected_reasons or not audit['consistent']):
        raise AssertionError('mixed source accounting differs from hand calculation')
    if reopened != result or replayed_audit != audit:
        raise AssertionError('reopen changed sealed mixed results')
    print('POINTWISE_SCENARIO ' + json.dumps(dict(
        case_id='v9.mixed_partitions', spec=spec, legacy_batch='mixed-legacy',
        cold_partition=cold['partition'], excluded_reads=excluded_reads,
        possible_offline=offline_outcome, recovered_rows=recovered['payload']['rows'],
        expected_rows=expected_rows, actual_rows=actual_rows,
        expected_groups=expected_groups, actual_groups=result['payload']['groups'],
        expected_reasons=expected_reasons, actual_reasons=audit['accounting']['reasons'],
        consistent=audit['consistent'],
        code_paths=['obslake/partition_spec.py', 'obslake/file_selection.py',
                    'obslake/file_builder.py', 'obslake/row_reader.py',
                    'obslake/source_accounting.py'],
        oracle='Hand calculation: file [0,3]={3,7}; 8<=t<10 empty, 7<=t<10 possible; '
               '4 sources = 2 selected + 1 null time + 1 FALSE'), sort_keys=True))


def demo(directory):
    manifest = json.loads(Path(__file__).with_name('run-manifest.json').read_text(encoding='utf-8'))
    sample = json.loads(Path(__file__).with_name(manifest['sample']).read_text(encoding='utf-8'))
    requests = manifest['requests']
    directory = Path(directory)
    with Lake(directory) as lake:
        first = lake.write(requests['batch_a'], iter(sample['A']))
        query = lake.create_query(requests['query'], first['snapshot_id'], 0, 1, ['or', ['eq', 'device', 7], ['eq', 'device', 9]])
        lake.write(requests['batch_b'], iter(sample['B']))
    with Lake(directory) as lake:
        result_id = lake.run_query(query)
        result = lake.read_result(result_id)
        body = result['payload']
        expected_groups = [
            dict(device=7, row_count=3, amount_count=2, sum_amount=0),
            dict(device=9, row_count=1, amount_count=0, sum_amount=None)]
        expected_ids = ['a02', 'a03', 'a04', 'a06']
        actual_ids = [row['id'] for row in body['rows']]
        if actual_ids != expected_ids or body['groups'] != expected_groups:
            raise AssertionError('demo delivery differs from hand calculation')
        had_export = lake.catalog.db.execute("SELECT 1 FROM receipts WHERE namespace='export' AND request=?", (requests['export'],)).fetchone() is not None
        receipt = lake.export_result(requests['export'], result_id, directory / manifest['export'])
        audit_id = lake.reconcile(requests['reconcile'], result_id)
        audit = lake.read_result(audit_id)['payload']
        expected_reasons = dict(null_time=1, outside_day=2, predicate_false=0, predicate_unknown=2, selected=4)
        if audit['accounting']['source_count'] != 9 or audit['accounting']['reasons'] != expected_reasons or not audit['consistent']:
            raise AssertionError('demo source accounting differs from hand calculation')
        if not had_export:
            exported = json.loads((directory / manifest['export']).read_text(encoding='utf-8'))
            if exported != result:
                raise AssertionError('demo downstream delivery is not the sealed result')
        print('POINTWISE_SCENARIO ' + json.dumps(dict(
            case_id='v8.reason_partition', input=sample, expected=dict(ids=expected_ids, groups=expected_groups),
            actual=dict(ids=actual_ids, groups=body['groups']),
            downstream_expected=dict(source_count=9, reasons=expected_reasons, consistent=True),
            downstream_actual=dict(source_count=audit['accounting']['source_count'], reasons=audit['accounting']['reasons'], consistent=audit['consistent']),
            code_paths=['obslake/intake.py','obslake/file_builder.py','obslake/query_jobs.py','obslake/aggregation.py','obslake/export.py','obslake/source_accounting.py'],
            oracle='Nine-row hand calculation in README; assertions precede this record'), sort_keys=True))
        print(json.dumps(dict(result_id=result_id, reconciliation_id=audit_id, export=receipt), sort_keys=True))

def main():
    parser = argparse.ArgumentParser(description='Lanwan observation day lake')
    parser.add_argument('command', choices=('init', 'demo', 'verify'))
    parser.add_argument('--data', type=Path, default=Path('demo-data'))
    args = parser.parse_args()
    if args.command == 'verify':
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / 'tests'), top_level_dir=str(Path(__file__).parent))
        from partition_examples import MixedPartitions
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(MixedPartitions))
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.command == 'init':
        with Lake(args.data) as lake:
            print(json.dumps(lake.snapshot(), sort_keys=True))
    else:
        demo(args.data)
        mixed_demo(Path(args.data) / 'mixed-partition-demo')
    return 0

if __name__ == '__main__':
    sys.exit(main())
