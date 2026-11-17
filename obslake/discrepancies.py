from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class Discrepancy:
    code: str
    key: object
    expected: object
    actual: object

    def as_dict(self):
        return asdict(self)

def compare_accounting(accounting, delivery):
    expected = accounting.as_dict()
    differences = []
    want_rows, got_rows = expected['rows'], delivery['rows']
    def source(row):
        return (row['batch_id'], row['ordinal'])
    want_keys, got_keys = [source(row) for row in want_rows], [source(row) for row in got_rows]
    want_set, got_set = set(want_keys), set(got_keys)
    if want_set != got_set or len(got_set) != len(got_keys):
        differences.append(Discrepancy('members', None, want_keys, got_keys))
    if want_keys != got_keys:
        differences.append(Discrepancy('order', None, want_keys, got_keys))
    actual_by_source = {source(row): row for row in got_rows}
    for row in want_rows:
        key = source(row)
        if key in actual_by_source and row != actual_by_source[key]:
            differences.append(Discrepancy('row_value', key, row, actual_by_source[key]))
    want_groups = {group['device']: group for group in expected['groups']}
    got_groups = {group['device']: group for group in delivery['groups']}
    devices = set(want_groups) | set(got_groups)
    for key in sorted(devices, key=lambda value: (value is not None, 0 if value is None else value)):
        left, right = want_groups.get(key), got_groups.get(key)
        if left is None or right is None:
            differences.append(Discrepancy('group_members', key, left, right))
        else:
            for field in ('row_count', 'amount_count', 'sum_amount'):
                if left[field] != right[field]:
                    differences.append(Discrepancy(field, key, left[field], right[field]))
    expected_order = [group['device'] for group in expected['groups']]
    actual_order = [group['device'] for group in delivery['groups']]
    if expected_order != actual_order:
        differences.append(Discrepancy('group_order', None, expected_order, actual_order))
    return tuple(differences)
