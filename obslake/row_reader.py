from .catalog import Unavailable
from .row_codec import decode_file
from .day_partition import day_number
from .day_pruning import row_in_day_range
from .predicates import matches_predicate
from .partition_spec import normalize_spec, normalize_partition, partition_values
from .file_selection import required_files


def expected_partition(snapshot, item):
    """The (spec, partition) a spec file's manifest binds, or None for V1."""
    sid = item.get('spec_id')
    if sid is None:
        return None
    spec = (snapshot.get('specs') or {}).get(sid)
    if spec is None:
        raise ValueError('missing spec for partitioned file')
    spec = normalize_spec(spec)
    return spec, normalize_partition(spec, item.get('partition'))


def read_manifest_file(item, batch, objects, snapshot=None):
    data = objects.read_verified(item['digest'], item['byte_count'])
    try:
        bound = expected_partition(snapshot, item) if snapshot is not None else None
        rows = decode_file(data)
        if len(rows) != item['row_count'] or not rows:
            raise ValueError('manifest count mismatch')
        if [row.ordinal for row in rows] != item['ordinals']:
            raise ValueError('manifest positions mismatch')
        ids = set()
        for row in rows:
            if row.batch_id != item['batch_id'] or row.batch_id != batch['batch_id']:
                raise ValueError('source batch mismatch')
            if not 0 <= row.ordinal < batch['row_count']:
                raise ValueError('source position outside batch')
            if bound is None:
                if day_number(row.observation.time) != item['day']:
                    raise ValueError('fixed day routing mismatch')
            else:
                spec, partition = bound
                if partition_values(spec, row.observation.time, row.observation.device) != partition:
                    raise ValueError('partition routing mismatch')
            if row.observation.id in ids:
                raise ValueError('duplicate observation id')
            ids.add(row.observation.id)
        return rows
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError) as exc:
        raise Unavailable('invalid object structure: ' + item['digest']) from exc


def read_selected_rows(snapshot, definition, objects):
    batches = {batch['batch_id']: batch for batch in snapshot['batches']}
    order = {batch['batch_id']: i for i, batch in enumerate(snapshot['batches'])}
    selected, positions, ids = [], set(), set()
    for item in required_files(snapshot, definition):
        batch = batches.get(item['batch_id'])
        if batch is None:
            raise Unavailable('orphan snapshot file')
        for row in read_manifest_file(item, batch, objects, snapshot):
            source = (row.batch_id, row.ordinal)
            observation_id = (row.batch_id, row.observation.id)
            if source in positions or observation_id in ids:
                raise Unavailable('duplicate source across objects')
            positions.add(source)
            ids.add(observation_id)
            if row_in_day_range(row.observation, definition['day_start'], definition['day_end']) and matches_predicate(definition['predicate'], row.observation):
                selected.append(row)
    selected.sort(key=lambda row: (order[row.batch_id], row.ordinal))
    return tuple(selected)
