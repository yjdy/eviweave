"""Explicit project-local provider evidence and bill import; never reads credentials."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re

from cost_accounting import DIMENSIONS, timestamp
from experiment_store import fingerprint, internal, read
import observability_store as store
from standalone import PROJECT, plain
from usage_normalize import (MAX_BYTES, Number, REDACTION_VERSION, event_selection,
                             parse, pointer, quantity, sanitize, sse, validate_mapping)

IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$')
ASSOCIATIONS = ('experiment_id', 'plan_id', 'launch_id', 'task_id', 'task_attempt_id', 'interaction_id')


def local_source(path):
    path = plain(path)
    if not path.is_relative_to(PROJECT) or not path.is_file():
        raise ValueError('import source must be a regular project-local file')
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('import source too large')
    return path.read_bytes()


def integer(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, Number)):
        raise ValueError(name + ' must be a JSON integer')
    if not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError(name + ' must be a JSON integer')
    return int(value)


def validate_call(call):
    if not isinstance(call, dict) or not isinstance(call.get('identity', {}), dict) or not isinstance(call.get('events', []), list):
        raise ValueError('call/identity objects and events array required')
    rid = call.get('request_attempt_id')
    if not isinstance(rid, str) or isinstance(rid, Number) or not IDENTIFIER.fullmatch(rid) or '..' in rid.split('/'):
        raise ValueError('explicit local request_attempt_id required')
    for name in ASSOCIATIONS:
        value = call.get(name)
        if value is not None and (not isinstance(value, str) or isinstance(value, Number) or not IDENTIFIER.fullmatch(value)):
            raise ValueError('invalid call association: ' + name)
    for name in ('started_at', 'ended_at'):
        if call.get(name) is not None:
            timestamp(call[name])
    if call.get('started_at') and call.get('ended_at') and timestamp(call['ended_at']) < timestamp(call['started_at']):
        raise ValueError('call end precedes start')
    if call.get('retry_of') == rid:
        raise ValueError('self-referential retry')
    call['retry_index'] = integer(call.get('retry_index', 0), 'retry_index')
    for field in ('http_status',):
        if call.get(field) is not None:
            call[field] = integer(call[field], field)
            if not 100 <= call[field] <= 599:
                raise ValueError('invalid HTTP status')
    for event in call.get('events', []):
        if not isinstance(event, dict) or not isinstance(event.get('payload'), dict):
            raise ValueError('event and payload objects required')
        event['sequence'] = integer(event['sequence'], 'event sequence')
    return call


def prepare_source(data, source_type, metadata=None):
    if source_type == 'responses-sse':
        frames = sse(data)
        # Normalize line endings before string-token redaction; sanitize all
        # frames together so reflected secrets in later frames are removed.
        normalized = data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').encode()
        safe = sanitize(normalized, frames)
        if not metadata:
            raise ValueError('SSE import requires explicit single-request metadata')
        events = [{'producer': 'raw-response', 'sequence': i + 1, 'event_id': f['id'],
                   'kind': f['payload'].get('type') or f['event'],
                   'terminal': f['payload'].get('type') in ('response.completed', 'response.failed', 'response.incomplete'),
                   'payload': f['payload']} for i, f in enumerate(sse(safe))]
        if events and not normalized.endswith(b'\n\n'):
            events[-1]['terminal'] = False
        calls = [dict(metadata, events=events)]
    elif source_type == 'responses-json':
        safe = sanitize(data)
        payload = parse(safe)
        if not metadata:
            raise ValueError('response import requires explicit single-request metadata')
        kind = 'response.' + payload.get('status', 'unknown')
        calls = [dict(metadata, events=[{'producer': 'raw-response', 'sequence': 1, 'event_id': None,
                                        'kind': kind, 'terminal': kind in ('response.completed', 'response.failed', 'response.incomplete'),
                                        'payload': {'response': payload}}])]
    elif source_type == 'calls':
        safe = sanitize(data)
        doc = parse(safe)
        if not isinstance(doc, dict) or doc.get('kind') != 'eviweave-call-import' or doc.get('schema_version') != '1':
            raise ValueError('unsupported call import envelope')
        calls = doc['requests']
    else:
        raise ValueError('unsupported provider evidence source type')
    if not isinstance(calls, list):
        raise ValueError('requests must be an array')
    calls = [validate_call(c) for c in calls]
    if len({c['request_attempt_id'] for c in calls}) != len(calls):
        raise ValueError('duplicate physical request identity in one import')
    return safe, calls


def bills_source(data, csv_format=False):
    if csv_format:
        rows = list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline='')))
        if not rows or any(None in row for row in rows):
            raise ValueError('invalid bill CSV')
        # JSON intermediate has only strings; bill numeric lexemes remain intact.
        safe = sanitize(json.dumps({'rows': rows}, ensure_ascii=False).encode())
        rows = parse(safe)['rows']
    else:
        safe = sanitize(data)
        rows = parse(safe)['rows']
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('bill rows must be an array of objects')
    seen = set()
    for row in rows:
        if not row.get('bill_id') or not IDENTIFIER.fullmatch(row['bill_id']) or row['bill_id'] in seen:
            raise ValueError('unique bill row identity required')
        seen.add(row['bill_id'])
        if row.get('grain') not in ('request', 'minute', 'hour', 'day', 'month', 'account'):
            raise ValueError('bill grain required')
        if row.get('amount_kind') not in ('actual_paid', 'payable', 'list', 'credit', 'tax'):
            raise ValueError('bill amount meaning required')
        if not row.get('currency') or not row.get('period_start') or not row.get('period_end'):
            raise ValueError('bill currency and period required')
        if timestamp(row['period_start']) >= timestamp(row['period_end']):
            raise ValueError('invalid billing period')
        quantity(row['amount'], integer=False)
        if row.get('request_attempt_id') and (row['grain'] != 'request' or not row.get('association_evidence')):
            raise ValueError('per-request billing requires strong association evidence')
    return safe, rows


def import_source(root, source, source_type, metadata=None):
    data = local_source(source)
    if source_type == 'billing':
        safe, rows = bills_source(data, Path(source).suffix.lower() == '.csv')
        descriptor = store.register_import(root, safe, source_type, {'redaction_version': REDACTION_VERSION})
        return descriptor
    safe, calls = prepare_source(data, source_type, metadata)
    # Metadata is sanitized independently and frozen with its interpretation.
    safe_metadata = json.loads(sanitize(json.dumps(metadata).encode()), parse_float=str) if metadata else None
    descriptor = store.register_import(root, safe, source_type,
        {'redaction_version': REDACTION_VERSION, 'metadata': safe_metadata,
         'request_ids': [c['request_attempt_id'] for c in calls]})
    return descriptor


def derive(root):
    doc = store.load(root)
    mapping = validate_mapping(read(root / 'resources/usage-mapping.json'))
    descriptors, gaps = store.imports(root)
    indexed, bills = {}, {}
    for descriptor in descriptors:
        raw = internal(root, descriptor['file']).read_bytes()
        if descriptor['source_type'] == 'billing':
            _, rows = bills_source(raw)
            for i, row in enumerate(rows):
                row = dict(row, evidence={'file': descriptor['file'], 'sha256': descriptor['sha256'], 'row': i})
                bid = row['bill_id']
                if bid in bills and {k:v for k,v in row.items() if k != 'evidence'} != {k:v for k,v in bills[bid].items() if k != 'evidence'}:
                    raise ValueError('conflicting bill identity; use new bill_id and supersedes')
                bills[bid] = row
            continue
        _, calls = prepare_source(raw, descriptor['source_type'], descriptor.get('metadata'))
        for i, call in enumerate(calls):
            rid = call['request_attempt_id']
            evidence = {'file': descriptor['file'], 'sha256': descriptor['sha256'], 'request_index': i}
            if rid in indexed:
                previous = indexed[rid]
                left = {k:v for k,v in call.items() if k != 'events'}
                right = {k:v for k,v in previous['call'].items() if k != 'events'}
                if left != right:
                    previous['conflicts'].append('conflicting source request metadata')
                previous['call']['events'].extend(call.get('events', []))
                previous['evidence'].append(evidence)
            else:
                indexed[rid] = {'call': call, 'evidence': [evidence], 'conflicts': []}
    result = []
    for rid, entry in indexed.items():
        call = entry['call']
        normalized, basis, conflicts = event_selection(call.get('events', []), mapping)
        conflicts += entry['conflicts']
        terminal = any(e.get('terminal') is True for e in call.get('events', []))
        amounts = []
        for rule in mapping.get('amounts', []):
            candidates = []
            for event in call.get('events', []):
                if event.get('terminal') is not True:
                    continue
                found, value = pointer(event['payload'], rule['path'])
                has_currency, currency = pointer(event['payload'], rule['currency_path'])
                if found and value is not None:
                    try:
                        quantity(value, integer=False)
                        candidates.append({'amount': str(value), 'currency': currency if has_currency else None,
                                           'path': rule['path'], 'meaning': rule['meaning']})
                    except ValueError:
                        conflicts.append('invalid provider amount')
            unique = {fingerprint(c): c for c in candidates}
            if len(unique) > 1:
                conflicts.append('conflicting provider amounts')
            amounts.extend(unique.values())
        classified = [a for a in amounts if a['meaning'] == 'provider_reported_charge' and a['currency']]
        provider_cost = {'amount': classified[0]['amount'] if len(classified) == 1 and not conflicts else None,
                         'currency': classified[0]['currency'] if len(classified) == 1 else None,
                         'complete': len(classified) == 1 and not conflicts,
                         'reason': None if len(classified) == 1 and not conflicts else 'provider amount missing/unclassified/conflicting',
                         'raw_amounts': amounts}
        record = {n: call.get(n) for n in (*ASSOCIATIONS, 'request_attempt_id', 'retry_of', 'retry_index', 'retry_scope',
                  'request_id', 'response_id', 'trace_id', 'span_id', 'started_at', 'ended_at', 'http_status',
                  'transport_status', 'response_status', 'error_category', 'termination_reason', 'server_received',
                  'duration_monotonic_ns', 'first_event_latency_ns', 'first_token_latency_ns')}
        record.update(schema_version=1, contract=store.CONTRACT,
                      identity={n: call.get('identity', {}).get(n) for n in (*DIMENSIONS, 'returned_model', 'revision')},
                      evidence=entry['evidence'], accounting_basis=basis,
                      normalized_usage=normalized, source_conflict=bool(conflicts), conflicts=conflicts,
                      capture_level=doc['capture_level'],
                      integrity={'start_durable': False, 'terminal_observed': terminal,
                                 'raw_usage_preserved': True, 'capture_gaps': [] if terminal else ['termination_unconfirmed']},
                      provider_reported=provider_cost)
        result.append(record)
    # A retry ID never identifies/deduplicates the original physical request.
    for record in result:
        prev = record['retry_of']
        chain = {record['request_attempt_id']}
        while prev:
            if prev not in indexed or prev in chain:
                record['source_conflict'] = True
                record['conflicts'].append('missing or cyclic retry association')
                break
            chain.add(prev)
            prev = indexed[prev]['call'].get('retry_of')
        if record['source_conflict']:
            record['provider_reported'].update(amount=None, complete=False, reason='conflicting attribution/source evidence')
    superseded = {}
    for bid, row in bills.items():
        prev = row.get('supersedes')
        if prev:
            if prev not in bills or prev == bid or prev in superseded:
                raise ValueError('invalid or competing bill correction')
            superseded[prev] = bid
    for bid in bills:
        visited, cursor = set(), bid
        while cursor in superseded:
            if cursor in visited:
                raise ValueError('cyclic billing correction')
            visited.add(cursor)
            cursor = superseded[cursor]
        bills[bid]['effective'] = bid not in superseded
    return result, list(bills.values()), gaps
