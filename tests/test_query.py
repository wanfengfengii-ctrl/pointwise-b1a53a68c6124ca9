import hashlib
from tests.test_acceptance import LakeCase, record
from obslake.catalog import Unavailable, NotFound
from obslake.row_codec import decode_file, encode_file
from obslake.records import LocatedRow
from obslake.row_reader import read_manifest_file

class TestQuery(LakeCase):
    def test_required_and_unrelated_object_fault(self):
        self.lake.write('a', [record('a',0), record('cold',86400), record('null',None)])
        self.lake.write('b', [record('b',1)])
        snapshot = self.lake.snapshot()
        necessary = [f for f in snapshot['files'] if f['day'] == 0]
        unrelated = [f for f in snapshot['files'] if f['day'] != 0]
        for item in unrelated:
            self.lake.objects.set_available(item['digest'], False)
        result = self.query('healthy')
        self.assertEqual([r['id'] for r in result['payload']['rows']], ['a','b'])
        self.assertEqual(set(self.lake.objects.read_trace), {f['digest'] for f in necessary})
        item = necessary[1]
        path = self.lake.objects.path_for(item['digest'])
        original = path.read_bytes()
        job = self.lake.create_query('fault', snapshot['id'], 0, 1)
        initial_results = self.count('results')
        for mode in ('offline','missing','corrupt'):
            if mode == 'offline':
                self.lake.objects.set_available(item['digest'], False)
            elif mode == 'missing':
                path.unlink()
            else:
                path.write_bytes(b'broken')
            with self.assertRaises(Unavailable):
                self.lake.run_query(job)
            self.assertEqual(self.count('results'), initial_results)
            self.assertEqual(self.lake.queries.read_job(job)['status'], 'failed')
            self.lake.objects.set_available(item['digest'], True)
            path.write_bytes(original)
        self.reopen()
        result_id = self.lake.run_query(job)
        self.assertEqual([r['id'] for r in self.lake.read_result(result_id)['payload']['rows']], ['a','b'])
        self.assertEqual(self.lake.run_query(job), result_id)
        self.assertEqual(self.count('results'), initial_results + 1)

    def test_structure_and_predicate_do_not_hide_required_files(self):
        self.lake.write('a', [record('x',0)])
        snapshot = self.lake.snapshot()
        item, batch = snapshot['files'][0], snapshot['batches'][0]
        original = decode_file(self.lake.objects.path_for(item['digest']).read_bytes())
        wrong_rows = [
            (LocatedRow('wrong', 0, original[0].observation),),
            (LocatedRow(batch['batch_id'], 99, original[0].observation),),
            (LocatedRow(batch['batch_id'], 0, type(original[0].observation)('x',86400,7,1)),)]
        for rows in wrong_rows:
            data = encode_file(rows)
            digest = self.lake.objects.seal(data)
            changed = dict(item, digest=digest, byte_count=len(data))
            with self.assertRaises(Unavailable):
                read_manifest_file(changed, batch, self.lake.objects)
        for changed in (dict(item, row_count=2), dict(item, ordinals=[1])):
            with self.assertRaises(Unavailable):
                read_manifest_file(changed, batch, self.lake.objects)
        self.lake.objects.set_available(item['digest'], False)
        job = self.lake.create_query('none', snapshot['id'], 0, 1, ['eq','device',999])
        with self.assertRaises(Unavailable):
            self.lake.run_query(job)
        empty = self.query('empty', start=0, end=0)
        self.assertEqual(empty['payload']['rows'], [])
        self.assertEqual(empty['payload']['groups'], [])

    def test_frozen_arguments_and_source_order(self):
        self.lake.write('a', [record('z',1,7),record('a',86400,7),record('b',0,9)])
        snapshot = self.lake.snapshot()
        predicate = ['or',['eq','device',7],['eq','device',9]]
        job = self.lake.create_query('q', snapshot['id'], 0, 2, predicate)
        predicate[1][2] = 999
        self.lake.write('b', [record('new',1)])
        self.reopen()
        result = self.lake.read_result(self.lake.run_query(job))
        self.assertEqual([row['id'] for row in result['payload']['rows']], ['z','a','b'])
        self.assertEqual(result['payload']['binding']['snapshot_id'], snapshot['id'])
        self.assertEqual(self.lake.create_query('q', snapshot['id'], 0, 2, ['or',['eq','device',7],['eq','device',9]]), job)
        with self.assertRaises(ValueError):
            self.lake.create_query('q', snapshot['id'], 0, 1)
        before = self.count('jobs')
        for start,end,pred in ((True,1,None),(2,1,None),(0,1,['eq','amount',2]),(0,1,['eq','time',True])):
            with self.assertRaises(ValueError):
                self.lake.create_query('bad', snapshot['id'], start,end,pred)
        with self.assertRaises(NotFound):
            self.lake.create_query('bad', 'unknown', 0, 1)
        self.assertEqual(self.count('jobs'), before)
