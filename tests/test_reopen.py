import contextlib
import io
import json
import sqlite3
from run import Lake, demo
from tests.test_acceptance import LakeCase, record
from obslake.catalog import NotFound, Unavailable

class TestReopen(LakeCase):
    def test_initialization_preserves_user_state(self):
        self.lake.write('r',[record()])
        before = self.lake.snapshot()
        user_file = self.data / 'notes.txt'
        user_file.write_text('user notes')
        self.reopen()
        self.assertEqual(self.lake.snapshot(),before)
        self.assertEqual(user_file.read_text(),'user notes')
        with self.assertRaises(NotFound):
            self.lake.snapshot('absent')
        item = before['files'][0]
        path = self.lake.objects.path_for(item['digest'])
        path.write_bytes(b'user changed object')
        self.reopen()
        self.assertEqual(path.read_bytes(),b'user changed object')
        job = self.lake.create_query('q',before['id'],0,1)
        with self.assertRaises(Unavailable):
            self.lake.run_query(job)

    def test_demo_repeat_and_user_edit(self):
        data = self.root / 'demo'
        with contextlib.redirect_stdout(io.StringIO()) as log:
            demo(data); demo(data)
        self.assertIn('POINTWISE_SCENARIO',log.getvalue())
        target = data / 'delivery.json'
        target.write_text('user edit')
        with contextlib.redirect_stdout(io.StringIO()):
            demo(data)
        self.assertEqual(target.read_text(),'user edit')
        with Lake(data) as lake:
            self.assertEqual(len(lake.snapshot()['batches']),2)

    def test_unknown_schema_rejected(self):
        self.close_lake()
        with sqlite3.connect(self.data / 'catalog.sqlite') as db:
            db.execute('PRAGMA user_version=999')
        with self.assertRaises(ValueError):
            Lake(self.data)
        with sqlite3.connect(self.data / 'catalog.sqlite') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],999)
