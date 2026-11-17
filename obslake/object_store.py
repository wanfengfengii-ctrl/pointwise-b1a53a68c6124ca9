import hashlib
import os
import tempfile
from pathlib import Path
from .catalog import Unavailable

class ObjectStore:
    def __init__(self, catalog):
        self.catalog = catalog
        self.directory = catalog.root / 'objects'
        self.directory.mkdir(exist_ok=True)
        self.read_trace = []

    def path_for(self, digest):
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('invalid object digest')
        return self.directory / digest

    def seal(self, data):
        if not isinstance(data, bytes):
            raise ValueError('object must be bytes')
        digest = hashlib.sha256(data).hexdigest()
        target = self.path_for(digest)
        if target.exists():
            if target.read_bytes() != data:
                raise Unavailable('existing content address is corrupt')
            return digest
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix='.stage-', delete=False) as stream:
                name = stream.name
                middle = len(data) // 2
                stream.write(data[:middle])
                self.catalog.checkpoint('object.mid_write')
                stream.write(data[middle:])
                stream.flush()
                os.fsync(stream.fileno())
            if Path(name).read_bytes() != data:
                raise Unavailable('seal verification failed')
            self.catalog.checkpoint('object.before_replace')
            os.replace(name, target)
            name = None
            self.catalog.checkpoint('object.sealed')
            return digest
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)

    def read_verified(self, digest, byte_count):
        self.read_trace.append(digest)
        try:
            if self.catalog.db.execute('SELECT 1 FROM offline WHERE digest=?', (digest,)).fetchone():
                raise Unavailable('object is offline: ' + digest)
            data = self.path_for(digest).read_bytes()
            if len(data) != byte_count or hashlib.sha256(data).hexdigest() != digest:
                raise Unavailable('object integrity failure: ' + digest)
            return data
        except (OSError, ValueError) as exc:
            if isinstance(exc, Unavailable):
                raise
            raise Unavailable('cannot read object: ' + digest) from exc

    def set_available(self, digest, available):
        self.path_for(digest)
        if type(available) is not bool:
            raise ValueError('availability must be boolean')
        with self.catalog.transaction():
            if available:
                self.catalog.db.execute('DELETE FROM offline WHERE digest=?', (digest,))
            else:
                self.catalog.db.execute('INSERT OR IGNORE INTO offline VALUES(?)', (digest,))
