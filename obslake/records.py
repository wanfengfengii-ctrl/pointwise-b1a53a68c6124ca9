from dataclasses import dataclass

FIELDS = {'id', 'time', 'device', 'amount'}

def validate_request_id(value):
    if not isinstance(value, str) or not value:
        raise ValueError('request id must be a nonempty string')
    return value

def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)

@dataclass(frozen=True)
class Observation:
    id: str
    time: object
    device: object
    amount: object

    def as_dict(self):
        return dict(id=self.id, time=self.time, device=self.device, amount=self.amount)

@dataclass(frozen=True)
class LocatedRow:
    batch_id: str
    ordinal: int
    observation: Observation

    def as_dict(self):
        return dict(batch_id=self.batch_id, ordinal=self.ordinal, **self.observation.as_dict())

def validate_record(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('record requires exactly id, time, device, amount')
    validate_request_id(value['id'])
    for name in ('time', 'device', 'amount'):
        if value[name] is not None and not integer(value[name]):
            raise ValueError(name + ' must be an integer or null')
    return Observation(**value)
