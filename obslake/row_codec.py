import json
import sys
from .records import LocatedRow, integer, validate_record, validate_request_id

# JSON is the persistence format for arbitrary precision business integers.
if hasattr(sys, 'set_int_max_str_digits'):
    sys.set_int_max_str_digits(0)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)

def encode_observation(row):
    return canonical(row.as_dict()).encode('utf-8')

def encode_file(rows):
    body = {'version': 1, 'rows': [row.as_dict() for row in rows]}
    return canonical(body).encode('utf-8')

def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result

def decode_file(data):
    body = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs)
    if not isinstance(body, dict) or set(body) != {'version', 'rows'}:
        raise ValueError('invalid file envelope')
    if type(body['version']) is not int or body['version'] != 1 or not isinstance(body['rows'], list):
        raise ValueError('unsupported file')
    result, seen = [], set()
    for item in body['rows']:
        if not isinstance(item, dict) or set(item) != {'batch_id', 'ordinal', 'id', 'time', 'device', 'amount'}:
            raise ValueError('invalid located row')
        batch = validate_request_id(item['batch_id'])
        pos = item['ordinal']
        if not integer(pos) or pos < 0 or (batch, pos) in seen:
            raise ValueError('invalid source position')
        seen.add((batch, pos))
        row = validate_record({key: item[key] for key in ('id', 'time', 'device', 'amount')})
        result.append(LocatedRow(batch, pos, row))
    if encode_file(result) != data:
        raise ValueError('noncanonical object')
    return tuple(result)
