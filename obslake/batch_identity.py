from dataclasses import dataclass
from hashlib import sha256
from .row_codec import canonical, encode_observation

@dataclass(frozen=True)
class BatchIdentity:
    digest: str
    batch_id: str

def identify_batch(request_id, rows):
    digest = sha256(b'obslake-ordered-batch-v1')
    digest.update(str(len(rows)).encode('ascii') + b':')
    for row in rows:
        data = encode_observation(row)
        digest.update(str(len(data)).encode('ascii') + b':' + data)
    content = digest.hexdigest()
    batch = sha256(canonical([request_id, content]).encode('utf-8')).hexdigest()
    return BatchIdentity(content, 'b-' + batch)
