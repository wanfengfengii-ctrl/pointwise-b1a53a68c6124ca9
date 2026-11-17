"""Ordered partition specs, partition values and per-file partition domains.

A spec is an ordered sequence of terms.  Each term is either a floor term
(value // width) or a mod term (value % modulus) on one of the two business
columns.  The module also turns a file's published partition values into the
exact (infinite) domain of column values that route to that file; that domain
is what query pruning analyses.
"""
import hashlib
from dataclasses import dataclass
from .records import integer
from .row_codec import canonical

COLUMNS = ('time', 'device')
FLOOR_KEYS = {'column', 'transform', 'width'}
MOD_KEYS = {'column', 'transform', 'modulus'}


class ImpossiblePartition(ValueError):
    """Published partition values that no legal value tuple can satisfy."""


def normalize_spec(spec):
    """Validate and copy a spec; returns a tuple of fresh term dictionaries."""
    if isinstance(spec, (str, bytes, dict)) or not isinstance(spec, (list, tuple)) or not spec:
        raise ValueError('partition spec must be a nonempty ordered list')
    normalized, seen = [], set()
    for item in spec:
        if not isinstance(item, dict):
            raise ValueError('partition term must be a dictionary')
        column = item.get('column')
        transform = item.get('transform')
        if not isinstance(column, str) or column not in COLUMNS:
            raise ValueError('partition column must be time or device')
        if transform == 'floor':
            if set(item) != FLOOR_KEYS:
                raise ValueError('floor term requires exactly column, transform, width')
            width = item['width']
            if not integer(width) or width <= 0:
                raise ValueError('floor width must be a positive integer')
            term = dict(column=column, transform='floor', width=width)
        elif transform == 'mod':
            if set(item) != MOD_KEYS:
                raise ValueError('mod term requires exactly column, transform, modulus')
            modulus = item['modulus']
            if not integer(modulus) or not 2 <= modulus <= 16:
                raise ValueError('mod modulus must be an integer between 2 and 16')
            term = dict(column=column, transform='mod', modulus=modulus)
        else:
            raise ValueError('unknown partition transform')
        key = (column, transform)
        if key in seen:
            raise ValueError('duplicate partition term for a column and transform')
        seen.add(key)
        normalized.append(term)
    return tuple(normalized)


def spec_body(spec):
    """Canonical persisted representation; distinct ordered specs differ."""
    return canonical([dict(term) for term in spec])


def spec_id(spec):
    return 'p-' + hashlib.sha256(spec_body(spec).encode('utf-8')).hexdigest()


def partition_values(spec, time, device):
    """Full ordered partition tuple for one accepted row."""
    result = []
    for term in spec:
        value = time if term['column'] == 'time' else device
        if value is None:
            result.append(None)
        elif term['transform'] == 'floor':
            result.append(value // term['width'])
        else:
            result.append(value % term['modulus'])
    return tuple(result)


def normalize_partition(spec, partition):
    """Validate published partition values against the spec's term order."""
    if isinstance(partition, (str, bytes, dict)) or not isinstance(partition, (list, tuple)):
        raise ValueError('partition must be an ordered list')
    if len(partition) != len(spec):
        raise ValueError('partition length does not match spec')
    result = []
    for term, value in zip(spec, partition):
        if value is None:
            result.append(None)
        elif integer(value):
            if term['transform'] == 'mod' and not 0 <= value < term['modulus']:
                raise ValueError('mod partition value outside modulus range')
            result.append(value)
        else:
            raise ValueError('partition values must be integers or null')
    return tuple(result)


@dataclass(frozen=True)
class ColumnDomain:
    """Allowed values for one column: optional null plus interval/residue."""
    none_allowed: bool
    none_only: bool
    lower: object
    upper: object
    modulus: object
    residue: object


def file_domains(spec, partition):
    """Exact value domain routed to a file, for every column.

    A term with a null partition value forces that column to be null; null
    values for *all* terms of the column are the only consistent manifest.
    Integer terms intersect into an interval and (at most one) residue class.
    """
    values = normalize_partition(spec, partition)
    domains = {}
    for column in COLUMNS:
        terms = [(term, value) for term, value in zip(spec, values)
                 if term['column'] == column]
        if terms and any(value is None for _, value in terms):
            if not all(value is None for _, value in terms):
                raise ValueError('mixed null and integer partition values')
            domains[column] = ColumnDomain(True, True, None, None, None, None)
            continue
        if not terms:
            domains[column] = ColumnDomain(True, False, None, None, None, None)
            continue
        lower = upper = modulus = residue = None
        for term, value in terms:
            if term['transform'] == 'floor':
                width = term['width']
                low = value * width
                high = low + width - 1
                lower = low if lower is None else max(lower, low)
                upper = high if upper is None else min(upper, high)
            else:
                modulus = term['modulus']
                residue = value
        if lower is not None and upper is not None and lower > upper:
            raise ImpossiblePartition('empty floor intersection')
        if modulus is not None:
            anchor = lower if lower is not None else (upper if upper is not None else 0)
            first = _next_in_domain(
                ColumnDomain(False, False, lower, upper, modulus, residue), lower, upper, anchor)
            if first is None:
                raise ImpossiblePartition('residue misses floor cell')
        domains[column] = ColumnDomain(False, False, lower, upper, modulus, residue)
    return domains


def _next_in_domain(domain, lower, upper, value):
    """Smallest domain integer >= value, or None when out of range."""
    if upper is not None and value > upper:
        return None
    if lower is not None and value < lower:
        value = lower
    if domain.modulus is not None:
        value += (domain.residue - value) % domain.modulus
    if upper is not None and value > upper:
        return None
    return value


def _previous_in_domain(domain, lower, upper, value):
    """Largest domain integer <= value, or None when out of range."""
    if lower is not None and value < lower:
        return None
    if upper is not None and value > upper:
        value = upper
    if domain.modulus is not None:
        value -= (value - domain.residue) % domain.modulus
    if lower is not None and value < lower:
        return None
    return value


def domain_candidates(domain, lower=None, upper=None, allow_none=True, constants=()):
    """Representatives covering every truth-distinct cell of the domain.

    The predicate only compares column values against a finite set of
    constants, so truth is constant on each point constant and on each open
    interval between neighbouring constants.  We return one representative per
    nonempty domain cell (plus null when the domain allows it).  This is an
    exact existential basis: a satisfying assignment exists iff one of these
    representatives satisfies the predicate.
    """
    candidates = set()
    if domain.none_allowed and allow_none:
        candidates.add(None)
    if domain.none_only:
        return sorted(candidates, key=lambda value: (value is not None, 0 if value is None else value))
    low = domain.lower if lower is None else (
        lower if domain.lower is None else max(domain.lower, lower))
    high = domain.upper if upper is None else (
        upper if domain.upper is None else min(domain.upper, upper))
    if low is not None and high is not None and low > high:
        return sorted(candidates, key=lambda value: (value is not None, 0 if value is None else value))
    anchors = set()
    for constant in constants:
        anchors.update((constant - 1, constant, constant + 1))
    if low is not None:
        anchors.add(low)
    if high is not None:
        anchors.add(high)
    for anchor in anchors:
        for value in (_next_in_domain(domain, low, high, anchor),
                      _previous_in_domain(domain, low, high, anchor),
                      _next_in_domain(domain, low, high, anchor + 1),
                      _previous_in_domain(domain, low, high, anchor - 1)):
            if value is not None:
                candidates.add(value)
    seed = low if low is not None else 0
    first = _next_in_domain(domain, low, high, seed)
    if first is not None:
        candidates.add(first)
    if high is not None:
        last = _previous_in_domain(domain, low, high, high)
        if last is not None:
            candidates.add(last)
    return sorted(candidates, key=lambda value: (value is not None, 0 if value is None else value))
