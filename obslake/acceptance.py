from dataclasses import dataclass
from .records import validate_record, validate_request_id
from .batch_identity import identify_batch

@dataclass(frozen=True)
class PreparedBatch:
    request_id: str
    rows: tuple
    identity: object

def prepare_batch(request_id, source):
    validate_request_id(request_id)
    rows, ids = [], set()
    for item in source:
        row = validate_record(item)
        if row.id in ids:
            raise ValueError('duplicate observation id within batch')
        ids.add(row.id)
        rows.append(row)
    rows = tuple(rows)
    return PreparedBatch(request_id, rows, identify_batch(request_id, rows))

def check_replay(receipts, prepared):
    return receipts.lookup('acceptance', prepared.request_id, prepared.identity.digest)
