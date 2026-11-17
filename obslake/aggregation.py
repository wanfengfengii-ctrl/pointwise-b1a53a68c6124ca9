from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class DeviceSummary:
    device: object
    row_count: int
    amount_count: int
    sum_amount: object

    def as_dict(self):
        return asdict(self)

def summarize_by_device(rows):
    groups = {}
    for row in rows:
        observation = row.observation
        state = groups.setdefault(observation.device, [0, 0, 0])
        state[0] += 1
        if observation.amount is not None:
            state[1] += 1
            state[2] += observation.amount
    summaries = []
    for device in sorted(groups, key=lambda value: (value is not None, 0 if value is None else value)):
        count, amount_count, total = groups[device]
        summaries.append(DeviceSummary(device, count, amount_count, total if amount_count else None))
    return tuple(summaries)
