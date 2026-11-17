from .records import integer
from .day_partition import day_number

def validate_day_range(start, end):
    if not integer(start) or not integer(end) or start > end:
        raise ValueError('expected ordered integer day endpoints')
    return start, end

def required_day_files(files, start, end):
    validate_day_range(start, end)
    return tuple(item for item in files if item['day'] is not None and start <= item['day'] < end)

def row_in_day_range(row, start, end):
    day = day_number(row.time)
    return day is not None and start <= day < end
