import hashlib
import json
from .catalog import NotFound
from .row_codec import canonical

class SnapshotStore:
    def __init__(self, catalog):
        self.catalog = catalog

    def snapshot(self, snapshot_id=None):
        if snapshot_id is None:
            snapshot_id = self.catalog.db.execute('SELECT snapshot_id FROM current_head WHERE singleton=1').fetchone()[0]
        if not isinstance(snapshot_id, str):
            raise NotFound(snapshot_id)
        row = self.catalog.db.execute('SELECT body FROM snapshots WHERE id=?', (snapshot_id,)).fetchone()
        if row is None:
            raise NotFound(snapshot_id)
        return json.loads(row[0])

    def plan_append(self, prepared, files):
        parent = self.snapshot()
        batch_id = prepared.identity.batch_id
        identity = canonical([parent['id'], batch_id])
        snapshot_id = 's-' + hashlib.sha256(identity.encode()).hexdigest()
        return dict(id=snapshot_id, parent=parent['id'],
                    batches=parent['batches'] + [dict(batch_id=batch_id, row_count=len(prepared.rows))],
                    files=parent['files'] + [item.as_dict() for item in files])

    def insert_snapshot(self, plan):
        if not self.catalog.db.in_transaction:
            raise RuntimeError('snapshot requires transaction')
        self.catalog.db.execute('INSERT INTO snapshots VALUES(?,?,?)', (plan['id'], plan['parent'], canonical(plan)))
        self.catalog.checkpoint('publication.snapshot')
        self.catalog.db.execute('UPDATE current_head SET snapshot_id=? WHERE singleton=1', (plan['id'],))
        self.catalog.checkpoint('publication.head')
