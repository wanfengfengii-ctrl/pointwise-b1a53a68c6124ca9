from dataclasses import dataclass
from .aggregation import summarize_by_device
from .row_codec import canonical
import json

@dataclass(frozen=True)
class DeliveryPayload:
    encoded: str

    def as_dict(self):
        return json.loads(self.encoded)

def verify_delivery_payload(payload):
    rows, groups = payload['rows'], payload['groups']
    keys = [(row['batch_id'], row['ordinal']) for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError('duplicate delivery source')
    devices = [group['device'] for group in groups]
    if len(set(devices)) != len(devices):
        raise ValueError('duplicate delivery group')
    if devices != sorted(devices, key=lambda value: (value is not None, 0 if value is None else value)):
        raise ValueError('unstable group order')
    if sum(group['row_count'] for group in groups) != len(rows):
        raise ValueError('delivery count mismatch')
    for group in groups:
        members = [row for row in rows if row['device'] == group['device']]
        amounts = [row['amount'] for row in members if row['amount'] is not None]
        if group['row_count'] != len(members) or group['amount_count'] != len(amounts):
            raise ValueError('group counts mismatch')
        if group['sum_amount'] != (sum(amounts) if amounts else None):
            raise ValueError('group total mismatch')
    if set(devices) != {row['device'] for row in rows}:
        raise ValueError('delivery device coverage mismatch')

def build_delivery_payload(job_id, definition, rows):
    body = dict(job_id=job_id, binding=definition, rows=[row.as_dict() for row in rows],
                groups=[group.as_dict() for group in summarize_by_device(rows)])
    verify_delivery_payload(body)
    return DeliveryPayload(canonical(body))
