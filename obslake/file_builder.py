from dataclasses import dataclass, asdict
from .records import LocatedRow, integer
from .day_partition import partition_rows, day_number
from .partition_spec import partition_values, normalize_partition, file_domains
from .row_codec import encode_file

@dataclass(frozen=True)
class DayFile:
    batch_id: str
    day: object
    digest: str
    byte_count: int
    row_count: int
    ordinals: tuple

    def as_dict(self):
        result = asdict(self)
        result['ordinals'] = list(self.ordinals)
        return result

@dataclass(frozen=True)
class SpecFile:
    batch_id: str
    spec_id: str
    partition: tuple
    digest: str
    byte_count: int
    row_count: int
    ordinals: tuple

    def as_dict(self):
        result = asdict(self)
        result['partition'] = list(self.partition)
        result['ordinals'] = list(self.ordinals)
        return result

def verify_manifest(prepared, files, spec=None):
    if spec is not None:
        return verify_spec_manifest(prepared, spec, files)
    positions, days = [], set()
    for item in files:
        if item.batch_id != prepared.identity.batch_id or item.day in days:
            raise ValueError('invalid manifest ownership or duplicate day')
        days.add(item.day)
        if item.row_count != len(item.ordinals) or not item.ordinals or tuple(sorted(item.ordinals)) != item.ordinals:
            raise ValueError('invalid manifest row count/order')
        if item.byte_count <= 0 or len(item.digest) != 64:
            raise ValueError('invalid content address metadata')
        for ordinal in item.ordinals:
            if not integer(ordinal) or not 0 <= ordinal < len(prepared.rows):
                raise ValueError('invalid manifest position')
            if day_number(prepared.rows[ordinal].time) != item.day:
                raise ValueError('manifest day mismatch')
        positions.extend(item.ordinals)
    if sorted(positions) != list(range(len(prepared.rows))):
        raise ValueError('manifest does not cover batch')

def verify_spec_manifest(prepared, spec, files):
    """Route and coverage check for a batch written under one spec."""
    positions, keys = [], set()
    for item in files:
        if item.batch_id != prepared.identity.batch_id:
            raise ValueError('invalid manifest ownership')
        key = normalize_partition(spec, item.partition)
        if key in keys:
            raise ValueError('duplicate partition file')
        keys.add(key)
        if item.row_count != len(item.ordinals) or not item.ordinals:
            raise ValueError('invalid manifest row count')
        if tuple(sorted(item.ordinals)) != item.ordinals:
            raise ValueError('invalid manifest order')
        if item.byte_count <= 0 or len(item.digest) != 64:
            raise ValueError('invalid content address metadata')
        for ordinal in item.ordinals:
            if not integer(ordinal) or not 0 <= ordinal < len(prepared.rows):
                raise ValueError('invalid manifest position')
            row = prepared.rows[ordinal]
            if partition_values(spec, row.time, row.device) != key:
                raise ValueError('manifest partition mismatch')
        positions.extend(item.ordinals)
    if sorted(positions) != list(range(len(prepared.rows))):
        raise ValueError('manifest does not cover batch')
    # The published partition must be interpretable without the writer.
    for item in files:
        file_domains(spec, item.partition)

def build_day_files(prepared, objects):
    located = tuple(LocatedRow(prepared.identity.batch_id, i, row) for i, row in enumerate(prepared.rows))
    files = []
    for day, group in partition_rows(located):
        data = encode_file(group)
        digest = objects.seal(data)
        files.append(DayFile(prepared.identity.batch_id, day, digest, len(data), len(group), tuple(row.ordinal for row in group)))
    files = tuple(files)
    verify_manifest(prepared, files)
    return files

def build_spec_files(prepared, spec, spec_id, objects):
    located = tuple(LocatedRow(prepared.identity.batch_id, i, row) for i, row in enumerate(prepared.rows))
    groups = {}
    for row in located:
        key = partition_values(spec, row.observation.time, row.observation.device)
        groups.setdefault(key, []).append(row)
    files = []
    for key, group in groups.items():
        data = encode_file(group)
        digest = objects.seal(data)
        files.append(SpecFile(prepared.identity.batch_id, spec_id, key, digest, len(data), len(group),
                              tuple(row.ordinal for row in group)))
    files = tuple(files)
    verify_spec_manifest(prepared, spec, files)
    return files
