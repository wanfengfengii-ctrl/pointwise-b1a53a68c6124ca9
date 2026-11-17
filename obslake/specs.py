"""Persistent partition-spec registry and the per-lake write-spec pointer."""
import json
from .catalog import NotFound
from .partition_spec import normalize_spec, spec_body, spec_id

# The no-spec writer means the fixed V1 layout: files carry a day value and no
# spec_id/partition metadata.
LEASE_SPEC = 'lease'


class SpecRegistry:
    def __init__(self, catalog):
        self.catalog = catalog

    def register(self, spec):
        spec = normalize_spec(spec)
        body = spec_body(spec)
        sid = spec_id(spec)
        with self.catalog.transaction():
            row = self.catalog.db.execute('SELECT body FROM partition_specs WHERE spec_id=?', (sid,)).fetchone()
            if row is not None:
                if row[0] != body:
                    raise ValueError('partition spec id collision')
                return sid
            self.catalog.db.execute('INSERT INTO partition_specs VALUES(?,?)', (sid, body))
            self.catalog.checkpoint('spec.registered')
        return sid

    def spec(self, sid):
        if not isinstance(sid, str):
            raise NotFound(sid)
        row = self.catalog.db.execute('SELECT body FROM partition_specs WHERE spec_id=?', (sid,)).fetchone()
        if row is None:
            raise NotFound(sid)
        return tuple(json.loads(row[0]))

    def read_write_spec(self):
        """Return (lease, spec_id, normalized spec or None)."""
        row = self.catalog.db.execute('SELECT spec_id FROM write_state WHERE singleton=1').fetchone()
        lease = row[0] if row is not None else None
        if lease is None:
            return LEASE_SPEC, None, None
        return 'spec', lease, self.spec(lease)

    def set_write_spec(self, sid):
        if not isinstance(sid, str) or not sid:
            raise ValueError('write spec id must be a nonempty string')
        self.spec(sid)  # NotFound for unknown ids, before any mutation
        with self.catalog.transaction():
            self.catalog.db.execute('UPDATE write_state SET spec_id=? WHERE singleton=1', (sid,))
            self.catalog.checkpoint('spec.selected')
        return None
