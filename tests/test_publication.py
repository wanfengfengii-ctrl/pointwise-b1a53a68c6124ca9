from tests.test_acceptance import LakeCase, record
from obslake.catalog import Unavailable

class TestPublication(LakeCase):
    def test_failure_then_retry(self):
        self.lake.write('initial', [record('old')])
        before = self.lake.snapshot()
        stages = ['publication.batch','publication.snapshot','publication.head',
                  'receipt.acceptance','publication.receipt','catalog.before_commit']
        for i, stage in enumerate(stages):
            marker = RuntimeError(stage)
            def fail(current, chosen=stage):
                if current == chosen:
                    raise marker
            counts = [self.count(t) for t in ('batches','snapshots','receipts')]
            self.lake.catalog.hook = fail
            with self.assertRaises(RuntimeError) as caught:
                self.lake.write('new', [record('new', time=86400)])
            self.assertIs(caught.exception, marker)
            self.lake.catalog.hook = None
            self.assertEqual(self.lake.snapshot(), before)
            self.assertEqual([self.count(t) for t in ('batches','snapshots','receipts')], counts)
            self.reopen()
            self.assertEqual(self.lake.snapshot(), before)
        receipt = self.lake.write('new', [record('new', time=86400)])
        self.assertEqual(receipt, self.lake.write('new', [record('new', time=86400)]))
        self.assertEqual(self.count('batches'), 2)
        self.assertEqual(self.lake.snapshot()['parent'], before['id'])

    def test_object_seal_failure(self):
        before = self.lake.snapshot()
        for stage in ('object.mid_write','object.before_replace','object.sealed'):
            def fail(current):
                if current == stage:
                    raise OSError(stage)
            self.lake.catalog.hook = fail
            # A different request gives a fresh content address for each seal attempt.
            with self.assertRaises(OSError):
                self.lake.write(stage, [record()])
            self.lake.catalog.hook = None
            self.assertEqual(self.lake.snapshot(), before)
            self.assertEqual(self.count('receipts'), 0)
            self.assertEqual(list(self.lake.objects.directory.glob('.stage-*')), [])
        self.lake.write('ok', [record()])
        self.assertEqual(self.count('batches'), 1)

    def test_result_and_query_creation_rollback(self):
        self.lake.write('r', [record()])
        snapshot = self.lake.snapshot()['id']
        for stage in ('query.created','receipt.query','catalog.before_commit'):
            def fail(current):
                if current == stage:
                    raise RuntimeError(stage)
            self.lake.catalog.hook = fail
            with self.assertRaises(RuntimeError):
                self.lake.create_query('q', snapshot, 0, 1)
            self.lake.catalog.hook = None
            self.assertEqual(self.count('jobs'), 0)
        job = self.lake.create_query('q', snapshot, 0, 1)
        for stage in ('result.insert','query.complete','catalog.before_commit'):
            def fail(current):
                if current == stage:
                    raise RuntimeError(stage)
            self.lake.catalog.hook = fail
            with self.assertRaises(RuntimeError):
                self.lake.run_query(job)
            self.lake.catalog.hook = None
            self.assertEqual(self.count('results'), 0)
            self.assertEqual(self.lake.queries.read_job(job)['status'], 'pending')
        result_id = self.lake.run_query(job)
        self.assertEqual(self.lake.run_query(job), result_id)
        self.assertEqual(self.count('results'), 1)
