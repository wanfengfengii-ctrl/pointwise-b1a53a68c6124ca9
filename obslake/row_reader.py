from .catalog import Unavailable
from .row_codec import decode_file
from .day_partition import day_number
from .day_pruning import required_day_files, row_in_day_range
from .predicates import matches_predicate

def read_manifest_file(item, batch, objects):
    data = objects.read_verified(item['digest'], item['byte_count'])
    try:
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
            if day_number(row.observation.time) != item['day']:
                raise ValueError('fixed day routing mismatch')
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
    files = required_day_files(snapshot['files'], definition['day_start'], definition['day_end'])
    for item in files:
        for row in read_manifest_file(item, batches[item['batch_id']], objects):
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
