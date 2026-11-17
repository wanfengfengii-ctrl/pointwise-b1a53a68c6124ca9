from dataclasses import dataclass, asdict
from .records import LocatedRow, integer
from .day_partition import partition_rows, day_number
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

def verify_manifest(prepared, files):
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
