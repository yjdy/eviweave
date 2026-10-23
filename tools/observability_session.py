"""Fail-closed online gate for the fixed native client.

No online profile is certified in contract v1. The offline importer is not a
substitute producer and cannot manufacture a pre-send durability guarantee.
"""
from experiment_store import read


def require_online_profile(profile, pricing=None):
    doc = read(profile)
    if doc.get('kind') != 'eviweave-observability-profile' or doc.get('schema_version') != 1:
        raise ValueError('unsupported observability profile')
    if doc.get('mode') not in ('full-request-evidence', 'request-counters'):
        raise ValueError('online mode must be explicitly declared')
    # A user-editable capabilities file is not proof of producer support.
    raise ValueError('G1: fixed Codex 0.145.0 has no certified online profile; '
                     'full raw usage/amount fidelity and durable-before-send are unsupported. '
                     'Use cost import for explicit offline evidence; no model was started.')
