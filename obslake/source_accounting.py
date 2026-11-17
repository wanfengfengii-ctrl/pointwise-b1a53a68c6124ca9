from dataclasses import dataclass
from .catalog import Unavailable
from .day_partition import day_number
from .predicates import evaluate_predicate, Truth
from .row_reader import read_manifest_file

@dataclass(frozen=True)
class SourceAccounting:
    source_count: int
    reasons: dict
    rows: tuple
    groups: tuple
    selected_amount_null: int
    selected_amount_nonnull: int

    def as_dict(self):
        return dict(source_count=self.source_count, reasons=dict(self.reasons),
                    rows=[row.as_dict() for row in self.rows], groups=list(self.groups),
                    selected_amount_null=self.selected_amount_null, selected_amount_nonnull=self.selected_amount_nonnull)

def account_sources(snapshot, definition, objects):
    reasons = dict(null_time=0, outside_day=0, predicate_false=0, predicate_unknown=0, selected=0)
    selected, groups, source_count = [], {}, 0
    for batch in snapshot['batches']:
        rows, partitions, ids = {}, set(), set()
        for item in snapshot['files']:
            if item['batch_id'] != batch['batch_id']:
                continue
            if item.get('spec_id') is None:
                key = ('day', item.get('day'))
            else:
                published = item.get('partition')
                key = (item['spec_id'], repr(published))
            if key in partitions:
                raise Unavailable('duplicate partition file')
            partitions.add(key)
            for row in read_manifest_file(item, batch, objects, snapshot):
                if row.ordinal in rows or row.observation.id in ids:
                    raise Unavailable('duplicate batch source')
                rows[row.ordinal] = row
                ids.add(row.observation.id)
        if sorted(rows) != list(range(batch['row_count'])):
            raise Unavailable('incomplete batch coverage')
        for ordinal in sorted(rows):
            row = rows[ordinal]
            source_count += 1
            obs = row.observation
            day = day_number(obs.time)
            if day is None:
                reason = 'null_time'
            elif not definition['day_start'] <= day < definition['day_end']:
                reason = 'outside_day'
            else:
                truth = evaluate_predicate(definition['predicate'], obs)
                reason = {Truth.TRUE: 'selected', Truth.FALSE: 'predicate_false', Truth.UNKNOWN: 'predicate_unknown'}[truth]
            reasons[reason] += 1
            if reason != 'selected':
                continue
            selected.append(row)
            state = groups.setdefault(obs.device, dict(device=obs.device, row_count=0, amount_count=0, sum_amount=None))
            state['row_count'] += 1
            if obs.amount is not None:
                state['amount_count'] += 1
                state['sum_amount'] = (0 if state['sum_amount'] is None else state['sum_amount']) + obs.amount
    known = {batch['batch_id'] for batch in snapshot['batches']}
    if any(item['batch_id'] not in known for item in snapshot['files']):
        raise Unavailable('orphan snapshot file')
    referenced = {item['spec_id'] for item in snapshot['files'] if item.get('spec_id') is not None}
    if not referenced <= set(snapshot.get('specs') or {}):
        raise Unavailable('snapshot omits a referenced spec')
    ordered = tuple(groups[key] for key in sorted(groups, key=lambda key: (key is not None, 0 if key is None else key)))
    nonnull = sum(state['amount_count'] for state in ordered)
    if source_count != sum(reasons.values()):
        raise ValueError('source accounting classification mismatch')
    return SourceAccounting(source_count, reasons, tuple(selected), ordered, len(selected) - nonnull, nonnull)
