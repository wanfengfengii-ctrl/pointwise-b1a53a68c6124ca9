DAY_SECONDS = 86400

def day_number(time):
    return None if time is None else time // DAY_SECONDS

def partition_rows(rows):
    groups = {}
    for row in rows:
        groups.setdefault(day_number(row.observation.time), []).append(row)
    return tuple((day, tuple(group)) for day, group in groups.items())
