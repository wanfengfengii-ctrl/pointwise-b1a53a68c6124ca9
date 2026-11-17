from enum import Enum
from .records import integer

class Truth(Enum):
    FALSE = 0
    TRUE = 1
    UNKNOWN = 2

COMPARISONS = {'lt', 'le', 'eq', 'ne', 'ge', 'gt'}
COLUMNS = {'time', 'device'}

def freeze_predicate(predicate):
    nodes, memo, active = [], {}, set()
    interned = {}
    def emit(node):
        signature = tuple(node)
        if signature not in interned:
            interned[signature] = len(nodes)
            nodes.append(node)
        return interned[signature]
    stack = [(predicate, False)]
    while stack:
        item, exiting = stack.pop()
        key = id(item)
        if key in memo:
            continue
        if exiting:
            active.remove(key)
            op = item[0]
            children = [memo[id(child)] for child in item[1:]]
            memo[key] = emit([op, *children])
            continue
        if item is None:
            memo[key] = emit(['true'])
            continue
        if not isinstance(item, (list, tuple)) or not item or not isinstance(item[0], str):
            raise ValueError('invalid predicate node')
        if key in active:
            raise ValueError('cyclic predicate')
        op = item[0]
        if op in COMPARISONS:
            if len(item) != 3 or not isinstance(item[1], str) or item[1] not in COLUMNS or not integer(item[2]):
                raise ValueError('invalid comparison')
            memo[key] = emit(list(item))
        elif op == 'is_null':
            if len(item) != 2 or not isinstance(item[1], str) or item[1] not in COLUMNS:
                raise ValueError('invalid null predicate')
            memo[key] = emit(list(item))
        elif op in ('and', 'or', 'not'):
            if len(item) != (2 if op == 'not' else 3):
                raise ValueError('invalid boolean arity')
            active.add(key)
            stack.append((item, True))
            stack.extend((child, False) for child in reversed(item[1:]))
        else:
            raise ValueError('unknown predicate operator')
    return {'nodes': nodes, 'root': memo[id(predicate)]}

def evaluate_predicate(frozen, observation):
    values = []
    for node in frozen['nodes']:
        op = node[0]
        if op == 'true':
            value = Truth.TRUE
        elif op == 'is_null':
            value = Truth.TRUE if getattr(observation, node[1]) is None else Truth.FALSE
        elif op in COMPARISONS:
            left, right = getattr(observation, node[1]), node[2]
            if left is None:
                value = Truth.UNKNOWN
            else:
                outcome = {'lt': lambda: left < right, 'le': lambda: left <= right,
                           'eq': lambda: left == right, 'ne': lambda: left != right,
                           'ge': lambda: left >= right, 'gt': lambda: left > right}[op]()
                value = Truth.TRUE if outcome else Truth.FALSE
        elif op == 'not':
            arg = values[node[1]]
            value = {Truth.TRUE: Truth.FALSE, Truth.FALSE: Truth.TRUE, Truth.UNKNOWN: Truth.UNKNOWN}[arg]
        else:
            left, right = values[node[1]], values[node[2]]
            if op == 'and':
                value = Truth.FALSE if Truth.FALSE in (left, right) else (Truth.TRUE if left == right == Truth.TRUE else Truth.UNKNOWN)
            else:
                value = Truth.TRUE if Truth.TRUE in (left, right) else (Truth.FALSE if left == right == Truth.FALSE else Truth.UNKNOWN)
        values.append(value)
    return values[frozen['root']]

def matches_predicate(frozen, observation):
    return evaluate_predicate(frozen, observation) is Truth.TRUE
