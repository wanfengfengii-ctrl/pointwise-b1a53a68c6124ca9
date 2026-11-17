import hashlib
import json
from .catalog import NotFound, Unavailable
from .records import validate_request_id
from .predicates import freeze_predicate
from .day_pruning import validate_day_range
from .row_reader import read_selected_rows
from .result_builder import build_delivery_payload
from .row_codec import canonical

class QueryJobs:
    def __init__(self, catalog, snapshots, receipts, objects, results):
        self.catalog, self.snapshots, self.receipts = catalog, snapshots, receipts
        self.objects, self.results = objects, results

    def create_query(self, request_id, snapshot_id, day_start, day_end, predicate=None):
        validate_request_id(request_id)
        validate_day_range(day_start, day_end)
        snapshot = self.snapshots.snapshot(snapshot_id)
        definition = dict(snapshot_id=snapshot['id'], day_start=day_start, day_end=day_end, predicate=freeze_predicate(predicate))
        identity = canonical(definition)
        job_id = 'q-' + hashlib.sha256(canonical([request_id, definition]).encode()).hexdigest()
        with self.catalog.transaction():
            replay = self.receipts.lookup('query', request_id, identity)
            if replay is not None:
                return replay['job_id']
            self.catalog.db.execute("INSERT INTO jobs VALUES(?,?,'pending',NULL,NULL)", (job_id, identity))
            self.catalog.checkpoint('query.created')
            self.receipts.insert('query', request_id, identity, dict(job_id=job_id))
        return job_id

    def read_job(self, job_id):
        if not isinstance(job_id, str):
            raise NotFound(job_id)
        row = self.catalog.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise NotFound(job_id)
        return dict(job_id=row['id'], definition=json.loads(row['definition']), status=row['status'], result_id=row['result_id'], error=row['error'])

    def run_query(self, job_id):
        job = self.read_job(job_id)
        if job['status'] == 'complete':
            return job['result_id']
        definition = job['definition']
        try:
            snapshot = self.snapshots.snapshot(definition['snapshot_id'])
            rows = read_selected_rows(snapshot, definition, self.objects)
            payload = build_delivery_payload(job_id, definition, rows)
        except Unavailable as exc:
            with self.catalog.transaction():
                self.catalog.db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (str(exc), job_id))
            raise
        result_id = 'r-' + job_id[2:]
        with self.catalog.transaction():
            self.results.publish_result(result_id, 'delivery', payload.as_dict())
            self.catalog.db.execute("UPDATE jobs SET status='complete',result_id=?,error=NULL WHERE id=?", (result_id, job_id))
            self.catalog.checkpoint('query.complete')
        return result_id
