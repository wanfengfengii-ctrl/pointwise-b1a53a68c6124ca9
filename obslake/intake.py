from .acceptance import prepare_batch, check_replay
from .file_builder import build_day_files
from .publication import publish_batch

class Intake:
    def __init__(self, catalog, snapshots, receipts, objects):
        self.catalog = catalog
        self.snapshots = snapshots
        self.receipts = receipts
        self.objects = objects

    def write(self, request_id, rows):
        prepared = prepare_batch(request_id, rows)
        replay = check_replay(self.receipts, prepared)
        if replay is not None:
            return replay
        files = build_day_files(prepared, self.objects)
        return publish_batch(self.catalog, self.snapshots, self.receipts, prepared, files)
