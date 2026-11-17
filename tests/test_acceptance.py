import copy
import tempfile
import unittest
from pathlib import Path
from run import Lake

def record(id='x', time=0, device=7, amount=1):
    return dict(id=id, time=time, device=device, amount=amount)

def sample_rows():
    values = [(-1,7,2),(0,7,None),(1,7,5),(86399,7,-5),(2,None,None),
              (3,9,None),(86400,7,100),(None,7,11),(4,None,99)]
    return [record('a%02d' % (i+1), *value) for i, value in enumerate(values)]

class LakeCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'lake'
        self.lake = Lake(self.data)
        self.addCleanup(self.close_lake)

    def close_lake(self):
        if self.lake is not None:
            self.lake.close()
            self.lake = None

    def reopen(self):
        self.close_lake()
        self.lake = Lake(self.data)

    def count(self, table):
        if table not in ('batches','snapshots','receipts','jobs','results'):
            raise ValueError(table)
        return self.lake.catalog.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def query(self, request='q', snapshot=None, start=0, end=1, predicate=None):
        if snapshot is None:
            snapshot = self.lake.snapshot()['id']
        job = self.lake.create_query(request, snapshot, start, end, predicate)
        result_id = self.lake.run_query(job)
        return self.lake.read_result(result_id)

class TestAcceptance(LakeCase):
    def test_partition_and_replay(self):
        rows = [record(str(i), t) for i, t in enumerate([-86401,-86400,-1,0,86399,86400,None])]
        first = self.lake.write('r', iter(rows))
        snapshot = self.lake.snapshot()
        files = snapshot['files']
        self.assertEqual([item['day'] for item in files], [-2,-1,0,1,None])
        self.assertEqual([item['ordinals'] for item in files], [[0],[1,2],[3,4],[5],[6]])
        self.assertEqual(self.lake.write('r', iter(rows)), first)
        self.assertEqual(self.count('batches'), 1)
        negative = self.query(start=-2, end=0)['payload']['rows']
        self.assertEqual([row['time'] for row in negative], [-86401,-86400,-1])
        self.assertEqual([row['ordinal'] for row in negative], [0,1,2])
        self.reopen()
        self.assertEqual(self.lake.write('r', iter(rows)), first)
        self.assertEqual(self.lake.snapshot(), snapshot)

    def test_conflicting_request(self):
        rows = [record('z', amount=None), record('a', amount=2)]
        self.lake.write('r', rows)
        before = self.lake.snapshot()
        for bad in (list(reversed(rows)), [record('z', amount=0), record('a', amount=2)]):
            with self.assertRaises(ValueError):
                self.lake.write('r', bad)
            self.assertEqual(self.lake.snapshot(), before)
            self.assertEqual(self.count('batches'), 1)
        self.assertEqual(self.count('receipts'), 1)

    def test_source_exception(self):
        before = self.lake.snapshot()
        marker = RuntimeError('source disconnected')
        class Once:
            def __init__(self, stop):
                self.stop, self.pos, self.iterated = stop, 0, False
            def __iter__(self):
                if self.iterated:
                    raise AssertionError('source was iterated twice')
                self.iterated = True
                return self
            def __next__(self):
                if self.pos == self.stop:
                    raise marker
                self.pos += 1
                return record(str(self.pos))
        for stop in (2, 9):
            with self.assertRaises(RuntimeError) as caught:
                self.lake.write('failed', Once(stop))
            self.assertIs(caught.exception, marker)
            self.assertEqual(self.lake.snapshot(), before)
            self.assertEqual(self.count('batches'), 0)
            self.assertEqual(self.count('receipts'), 0)
            self.reopen()
            self.assertEqual(self.lake.snapshot(), before)
        receipt = self.lake.write('failed', [record('1'), record('2')])
        self.assertEqual(receipt['row_count'], 2)
        with self.assertRaises(RuntimeError) as caught:
            self.lake.write('failed', Once(2))
        self.assertIs(caught.exception, marker)
        self.assertEqual(self.count('batches'), 1)

    def test_empty_and_cross_batch_ids(self):
        first = self.lake.write('empty', [])
        self.assertEqual(first, self.lake.write('empty', iter([])))
        self.assertEqual(self.lake.snapshot()['files'], [])
        self.lake.write('a', [record()])
        self.lake.write('b', [record()])
        rows = self.query()['payload']['rows']
        self.assertEqual([r['id'] for r in rows], ['x','x'])
        self.assertNotEqual(rows[0]['batch_id'], rows[1]['batch_id'])
        self.assertEqual(self.count('batches'), 3)
