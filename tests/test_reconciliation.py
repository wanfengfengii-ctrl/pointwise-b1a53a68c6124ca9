import copy
import json
from tests.test_acceptance import LakeCase, sample_rows, record
from obslake.catalog import Unavailable
from obslake.source_accounting import account_sources
from obslake.discrepancies import compare_accounting

class TestReconciliation(LakeCase):
    def make_delivery(self):
        receipt = self.lake.write('A', sample_rows())
        return self.query('Q', receipt['snapshot_id'], predicate=['or',['eq','device',7],['eq','device',9]])

    def test_source_counts_and_reopen(self):
        result = self.make_delivery()
        audit_id = self.lake.reconcile('C', result['result_id'])
        audit = self.lake.read_result(audit_id)
        body = audit['payload']
        counts = body['accounting']
        expected = dict(null_time=1,outside_day=2,predicate_false=0,predicate_unknown=2,selected=4)
        self.assertEqual(counts['source_count'], 9)
        self.assertEqual(counts['reasons'], expected)
        self.assertEqual(counts['selected_amount_null'], 2)
        self.assertEqual(counts['selected_amount_nonnull'], 2)
        self.assertEqual(counts['groups'], [
            dict(device=7,row_count=3,amount_count=2,sum_amount=0),
            dict(device=9,row_count=1,amount_count=0,sum_amount=None)])
        self.assertTrue(body['consistent'])
        self.assertEqual(body['discrepancies'], [])
        self.assertEqual(counts['source_count'], sum(counts['reasons'].values()))
        self.lake.write('B', [record('new',amount=1000)])
        self.reopen()
        self.assertEqual(self.lake.read_result(audit_id), audit)
        for item in self.lake.snapshot(result['payload']['binding']['snapshot_id'])['files']:
            self.lake.objects.set_available(item['digest'], False)
        self.assertEqual(self.lake.reconcile('C', result['result_id']), audit_id)
        self.assertEqual(self.lake.run_query(result['payload']['job_id']), result['result_id'])
        other = self.query('empty',start=0,end=0)
        with self.assertRaises(ValueError):
            self.lake.reconcile('C', other['result_id'])
        print('POINTWISE_SCENARIO ' + json.dumps(dict(
            case_id='v8.reason_partition', input=sample_rows(),
            expected=dict(source_count=9,reasons=expected),
            actual=dict(source_count=counts['source_count'],reasons=counts['reasons']),
            downstream_expected=dict(consistent=True,discrepancies=[]),
            downstream_actual=dict(consistent=body['consistent'],discrepancies=body['discrepancies']),
            code_paths=['obslake/reconcile.py','obslake/source_accounting.py','obslake/discrepancies.py','obslake/reconciliation_results.py'],
            oracle='Independent hand enumeration: 9 = 4 + 1 + 2 + 0 + 2'), sort_keys=True))

    def test_source_unavailable_and_retry(self):
        result = self.make_delivery()
        snapshot = self.lake.snapshot(result['payload']['binding']['snapshot_id'])
        cold = next(item for item in snapshot['files'] if item['day'] == 1)
        self.lake.objects.set_available(cold['digest'], False)
        before = self.count('results')
        with self.assertRaises(Unavailable):
            self.lake.reconcile('C', result['result_id'])
        self.assertEqual(self.count('results'), before)
        self.assertIsNone(self.lake.catalog.db.execute("SELECT 1 FROM receipts WHERE namespace='reconcile' AND request='C'").fetchone())
        self.lake.objects.set_available(cold['digest'], True)
        for stage in ('result.insert','receipt.reconcile','reconciliation.saved','catalog.before_commit'):
            def fail(current):
                if current == stage:
                    raise RuntimeError(stage)
            self.lake.catalog.hook = fail
            with self.assertRaises(RuntimeError):
                self.lake.reconcile('C', result['result_id'])
            self.lake.catalog.hook = None
            self.assertEqual(self.count('results'), before)
        audit = self.lake.reconcile('C', result['result_id'])
        self.assertTrue(self.lake.read_result(audit)['payload']['consistent'])
        with self.assertRaises(ValueError):
            self.lake.reconcile('recursive', audit)

    def test_discrepancies(self):
        self.lake.write('large', [record('large1',1,8,2**60+1), record('large2',1,8,-2**60)])
        result = self.make_delivery()
        payload = result['payload']
        accounting = account_sources(self.lake.snapshot(payload['binding']['snapshot_id']), payload['binding'], self.lake.objects)
        self.assertEqual(compare_accounting(accounting,payload), ())
        cases = []
        changed = copy.deepcopy(payload); changed['rows'].pop(0); cases.append((changed,'members'))
        changed = copy.deepcopy(payload); changed['rows'].reverse(); cases.append((changed,'order'))
        changed = copy.deepcopy(payload); changed['groups'].pop(); cases.append((changed,'group_members'))
        changed = copy.deepcopy(payload); changed['groups'][-1]['sum_amount']=0; cases.append((changed,'sum_amount'))
        changed = copy.deepcopy(payload); changed['groups'][0]['amount_count']=3; cases.append((changed,'amount_count'))
        # Exchange +5 and -5 while retaining identical totals.
        changed = copy.deepcopy(payload)
        changed['rows'][1]['amount']=-5; changed['rows'][2]['amount']=5
        cases.append((changed,'row_value'))
        for changed, code in cases:
            differences = compare_accounting(accounting,changed)
            self.assertIn(code,[d.code for d in differences])
        large = self.query('large-q',predicate=['eq','device',8])['payload']
        source = account_sources(self.lake.snapshot(large['binding']['snapshot_id']),large['binding'],self.lake.objects)
        self.assertEqual(source.groups[0]['sum_amount'],1)
        large['groups'][0]['sum_amount']=0
        self.assertIn('sum_amount',[d.code for d in compare_accounting(source,large)])
