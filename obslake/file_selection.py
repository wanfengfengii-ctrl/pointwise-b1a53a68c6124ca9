"""Exact per-file possibility analysis derived only from manifest metadata.

A file's manifest binds it to a value domain for each business column: a day
interval for V1 files, or the exact inverse of every partition term for spec
files.  A query reads a file if and only if some assignment of *legal column
values* inside that domain lies in the day range and makes the predicate TRUE.
Actual stored rows are never consulted, and each branch of a boolean predicate
is evaluated on the same joint (time, device) assignment.
"""
from .records import Observation, integer
from .predicates import matches_predicate, COMPARISONS
from .partition_spec import (ColumnDomain, ImpossiblePartition, normalize_spec,
                             file_domains, domain_candidates)

DAY_SECONDS = 86400


def file_domains_for(snapshot, item):
    """Exact column domains for one manifest file; ValueError if unreadable."""
    sid = item.get('spec_id')
    if sid is None:
        day = item.get('day')
        if day is None:
            time_domain = ColumnDomain(True, True, None, None, None, None)
        elif integer(day):
            low = day * DAY_SECONDS
            time_domain = ColumnDomain(False, False, low, low + DAY_SECONDS - 1, None, None)
        else:
            raise ValueError('invalid day value')
        return {'time': time_domain,
                'device': ColumnDomain(True, False, None, None, None, None)}
    spec = (snapshot.get('specs') or {}).get(sid)
    if spec is None:
        raise ValueError('snapshot does not define referenced spec')
    return file_domains(normalize_spec(spec), item.get('partition'))


def _constants(frozen):
    constants = {'time': set(), 'device': set()}
    for node in frozen['nodes']:
        if node[0] in COMPARISONS:
            constants[node[1]].add(node[2])
    return constants


def file_may_select(snapshot, item, definition):
    """True only when a legal in-domain assignment can satisfy day+predicate."""
    start, end = definition['day_start'], definition['day_end']
    if start == end:
        return False
    try:
        domains = file_domains_for(snapshot, item)
    except ImpossiblePartition:
        # Values that violate the spec cannot occur in any accepted row, so a
        # well-formed snapshot can never store rows there: nothing to read.
        return False
    except (ValueError, TypeError, KeyError, IndexError):
        # Uninterpretable metadata cannot prove impossibility: request it and
        # let structural verification report Unavailable.
        return True
    units = _constants(definition['predicate'])
    time_values = domain_candidates(domains['time'], start * DAY_SECONDS,
                                    end * DAY_SECONDS - 1, allow_none=False,
                                    constants=units['time'])
    if not time_values:
        return False
    device_values = domain_candidates(domains['device'], None, None,
                                      allow_none=True, constants=units['device'])
    if not device_values:
        return False
    for time in time_values:
        for device in device_values:
            if matches_predicate(definition['predicate'], Observation('', time, device, 0)):
                return True
    return False


def required_files(snapshot, definition):
    return tuple(item for item in snapshot['files'] if file_may_select(snapshot, item, definition))
