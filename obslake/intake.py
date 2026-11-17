from .acceptance import prepare_batch, check_replay
from .file_builder import build_day_files, build_spec_files
from .publication import publish_batch

class Intake:
    def __init__(self, catalog, snapshots, receipts, objects, specs):
        self.catalog = catalog
        self.snapshots = snapshots
        self.receipts = receipts
        self.objects = objects
        self.specs = specs

    def write(self, request_id, rows):
        prepared = prepare_batch(request_id, rows)
        replay = check_replay(self.receipts, prepared)
        if replay is not None:
            return replay
        lease, spec_id, spec = self.specs.read_write_spec()
        if lease == 'spec':
            files = build_spec_files(prepared, spec, spec_id, self.objects)
            return publish_batch(self.catalog, self.snapshots, self.receipts, prepared, files, spec)
        files = build_day_files(prepared, self.objects)
        return publish_batch(self.catalog, self.snapshots, self.receipts, prepared, files)
