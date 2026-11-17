import hashlib
import json
from tests.test_acceptance import LakeCase, sample_rows, record
from obslake.row_codec import canonical
from obslake.catalog import NotFound

class TestDelivery(LakeCase):
    def test_exact_null_and_zero(self):
        first = self.lake.write('A', sample_rows())
        predicate = ['or',['eq','device',7],['eq','device',9]]
        job = self.lake.create_query('Q', first['snapshot_id'], 0, 1, predicate)
        self.lake.write('B', [record('B',5,7,1000)])
        self.reopen()
        result_id = self.lake.run_query(job)
        result = self.lake.read_result(result_id)
        self.assertEqual([row['id'] for row in result['payload']['rows']], ['a02','a03','a04','a06'])
        self.assertEqual(result['payload']['groups'], [
            dict(device=7,row_count=3,amount_count=2,sum_amount=0),
            dict(device=9,row_count=1,amount_count=0,sum_amount=None)])
        self.assertEqual(result['payload']['binding']['snapshot_id'], first['snapshot_id'])
        self.assertEqual(self.lake.run_query(job), result_id)
        for item in self.lake.snapshot(first['snapshot_id'])['files']:
            self.lake.objects.set_available(item['digest'], False)
        self.assertEqual(self.lake.run_query(job), result_id)
        saved = self.lake.read_result(result_id)
        result['payload']['rows'].clear()
        result['payload']['groups'][0]['sum_amount'] = 999
        snapshot_copy = self.lake.snapshot(first['snapshot_id'])
        snapshot_copy['batches'].clear()
        snapshot_copy['files'][0]['ordinals'].clear()
        self.assertEqual(self.lake.read_result(result_id), saved)
        self.assertEqual(len(self.lake.snapshot(first['snapshot_id'])['batches']), 1)
        self.assertEqual(self.lake.snapshot(first['snapshot_id'])['files'][0]['ordinals'], [0])

    def test_null_device_large_integer_and_stable_groups(self):
        values = [record('z',1,7,2**60+1),record('a',0,None,None),
                  record('b',1,7,-2**60),record('c',1,-3,0)]
        self.lake.write('r', values)
        result = self.query()['payload']
        self.assertEqual([r['id'] for r in result['rows']], ['z','a','b','c'])
        self.assertEqual(result['groups'], [
            dict(device=None,row_count=1,amount_count=0,sum_amount=None),
            dict(device=-3,row_count=1,amount_count=1,sum_amount=0),
            dict(device=7,row_count=2,amount_count=2,sum_amount=1)])
        receipt = self.lake.export_result('e', self.query('second')['result_id'], self.root / 'out.json')
        exported = json.loads((self.root / 'out.json').read_text())
        self.assertEqual(exported['payload']['groups'][-1]['sum_amount'], 1)
        self.assertIs(type(exported['payload']['groups'][-1]['sum_amount']), int)
        self.assertEqual(receipt['digest'], hashlib.sha256((self.root / 'out.json').read_bytes()).hexdigest())
        self.reopen()
        self.assertEqual(self.query('q')['payload'], result)

    def test_export_failure(self):
        self.lake.write('r', [record()])
        first = self.query('first')
        target = self.root / 'delivery.json'
        original_receipt = self.lake.export_result('old', first['result_id'], target)
        original = target.read_bytes()
        self.lake.write('r2', [record('new',amount=2)])
        second = self.query('second')
        for stage in ('export.mid_write','export.before_replace','export.after_replace','receipt.export','catalog.before_commit'):
            marker = OSError(stage)
            def fail(current):
                if current == stage:
                    raise marker
            self.lake.catalog.hook = fail
            with self.assertRaises(OSError) as caught:
                self.lake.export_result('new', second['result_id'], target)
            self.assertIs(caught.exception, marker)
            self.lake.catalog.hook = None
            self.assertEqual(target.read_bytes(), original)
            self.assertIsNone(self.lake.catalog.db.execute("SELECT 1 FROM receipts WHERE namespace='export' AND request='new'").fetchone())
            self.assertEqual(self.lake.read_result(first['result_id']), first)
        receipt = self.lake.export_result('new', second['result_id'], target)
        self.assertNotEqual(receipt['digest'], original_receipt['digest'])
        self.assertEqual(json.loads(target.read_text()), second)
        self.assertEqual(list(self.root.glob('.delivery-*')), [])
        self.assertEqual(list(self.root.glob('.previous-*')), [])
        target.write_bytes(b'user edit')
        self.assertEqual(self.lake.export_result('new', second['result_id'], target), receipt)
        self.assertEqual(target.read_bytes(), b'user edit')
        with self.assertRaises(ValueError):
            self.lake.export_result('new', first['result_id'], target)
        self.reopen()
        self.assertEqual(self.lake.export_result('new', second['result_id'], target), receipt)
        self.assertEqual(target.read_bytes(), b'user edit')

    def test_export_failure_without_old_target(self):
        self.lake.write('r', [record()])
        result = self.query()
        target = self.root / 'fresh.json'
        def fail(stage):
            if stage == 'export.after_replace':
                raise OSError('interrupted')
        self.lake.catalog.hook = fail
        with self.assertRaises(OSError):
            self.lake.export_result('e', result['result_id'], target)
        self.lake.catalog.hook = None
        self.assertFalse(target.exists())
        self.lake.export_result('e', result['result_id'], target)
        self.assertTrue(target.exists())
        with self.assertRaises(NotFound):
            self.lake.read_result('missing')
