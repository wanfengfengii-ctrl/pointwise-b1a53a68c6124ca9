from .acceptance import check_replay
from .file_builder import verify_manifest
from .row_codec import canonical

def publish_batch(catalog, snapshots, receipts, prepared, files, spec=None):
    verify_manifest(prepared, files, spec)
    with catalog.transaction():
        replay = check_replay(receipts, prepared)
        if replay is not None:
            return replay
        plan = snapshots.plan_append(prepared, files)
        batch_id = prepared.identity.batch_id
        catalog.db.execute('INSERT INTO batches VALUES(?,?,?,?,?)', (batch_id, prepared.request_id, prepared.identity.digest, len(prepared.rows), canonical([f.as_dict() for f in files])))
        catalog.checkpoint('publication.batch')
        snapshots.insert_snapshot(plan)
        response = dict(batch_id=batch_id, snapshot_id=plan['id'], request_id=prepared.request_id, row_count=len(prepared.rows))
        receipts.insert('acceptance', prepared.request_id, prepared.identity.digest, response)
        catalog.checkpoint('publication.receipt')
    return response
