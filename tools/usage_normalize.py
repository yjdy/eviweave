"""Lossless JSON/SSE parsing, credential redaction and explicit usage mapping.

No provider field is inferred by name. Raw numeric spellings live in the
sanitized evidence bytes; derived quantities are decimal strings.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re

MAX_BYTES = 8 * 1024 * 1024
REDACTION_VERSION = 'credential-redaction-v1'


class Number(str):
    """A JSON number, distinguished from a JSON string without passing through float."""


def parse(data):
    if isinstance(data, bytes):
        if len(data) > MAX_BYTES:
            raise ValueError('evidence exceeds maximum byte size')
        data = data.decode('utf-8', errors='strict')
    if len(data.encode()) > MAX_BYTES:
        raise ValueError('evidence exceeds maximum byte size')
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError('duplicate JSON key')
            obj[key] = value
        return obj
    try:
        return json.loads(data, parse_int=Number, parse_float=Number, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))
    except (RecursionError, UnicodeError) as exc:
        raise ValueError('invalid or overly nested JSON') from exc


SENSITIVE = re.compile(r'(?i)^(authorization|proxy.authorization|cookie|set.cookie|.*(?:api[_-]?key|access[_-]?token|secret|password))$')
STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*')
KEY = re.compile(r'\b(?:sk|sess)-[A-Za-z0-9_-]{8,}\b')


def sanitize(data, documents=None):
    """Redact string tokens only, retaining all raw JSON numeric lexemes.

    Gather sensitive values first across all SSE frames, then redact reflections
    anywhere in the material, including JSON-escaped reflections.
    """
    doc = parse(data) if documents is None else documents
    secrets = set()
    def gather(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if SENSITIVE.match(key) and isinstance(child, str) and child:
                    secrets.add(str(child))
                    if str(child).lower().startswith('bearer '):
                        secrets.add(str(child)[7:])
                gather(child)
        elif isinstance(value, list):
            for child in value:
                gather(child)
    gather(doc)
    fragments = []
    def gather_fragments(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('delta', 'text') and isinstance(child, str) and not isinstance(child, Number):
                    fragments.append(child)
                else:
                    gather_fragments(child)
        elif isinstance(value, list):
            for child in value:
                gather_fragments(child)
    gather_fragments(doc)
    combined = ''.join(fragments)
    spans = [(m.start(), m.end()) for pattern in (BEARER, KEY) for m in pattern.finditer(combined)]
    for secret in secrets:
        start = 0
        while (position := combined.find(secret, start)) >= 0:
            spans.append((position, position + len(secret)))
            start = position + max(len(secret), 1)
    redacted_fragments, cursor = set(), 0
    for fragment in fragments:
        if any(start < cursor + len(fragment) and end > cursor for start, end in spans):
            redacted_fragments.add(fragment)
        cursor += len(fragment)
    def replacement(match):
        value = json.loads(match.group())
        if value in redacted_fragments:
            return '"[REDACTED]"'
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        value = BEARER.sub('Bearer [REDACTED]', value)
        value = KEY.sub('[REDACTED]', value)
        return json.dumps(value, ensure_ascii=False)
    return STRING.sub(replacement, data.decode('utf-8')).encode()


def sse(data):
    if len(data) > MAX_BYTES:
        raise ValueError('SSE exceeds maximum byte size')
    text = data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    frames = []
    for block in text.split('\n\n'):
        if not block.strip():
            continue
        parts, name, eid = [], None, None
        for line in block.split('\n'):
            if line.startswith(':'):
                continue
            field, sep, value = line.partition(':')
            if sep and value.startswith(' '):
                value = value[1:]
            if field == 'data':
                parts.append(value)
            elif field == 'event':
                name = value
            elif field == 'id':
                eid = value
        if not parts:
            continue
        payload = '\n'.join(parts)
        if payload == '[DONE]':
            continue
        frames.append({'event': name, 'id': eid, 'payload': parse(payload)})
    return frames


def pointer(value, path):
    if path == '':
        return True, value
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('mapping paths must be JSON Pointers')
    for token in path[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise ValueError('invalid JSON Pointer escape')
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            return False, None
    return True, value


def quantity(value, *, integer=True):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('invalid quantity type')
    try:
        n = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('invalid decimal quantity') from exc
    if not n.is_finite() or n < 0 or (integer and n != n.to_integral_value()):
        raise ValueError('invalid nonnegative quantity')
    if len(n.as_tuple().digits) > 1000 or abs(n.as_tuple().exponent) > 1000:
        raise ValueError('quantity precision exceeds supported bounds')
    return n


def validate_mapping(mapping):
    if not isinstance(mapping, dict) or mapping.get('kind') != 'eviweave-usage-mapping' or mapping.get('schema_version') != 1:
        raise ValueError('unsupported usage mapping')
    if mapping.get('event_semantics') not in ('cumulative', 'incremental'):
        raise ValueError('mapping must declare cumulative/incremental semantics')
    fields = mapping.get('fields')
    if not isinstance(fields, dict) or not fields:
        raise ValueError('mapping fields required')
    for name, rule in fields.items():
        if not isinstance(rule, dict) or rule.get('unit') not in ('token', 'image', 'second', 'request'):
            raise ValueError('invalid mapped unit')
        pointer({}, rule['path'])
        parent = rule.get('included_in')
        if parent and (parent not in fields or parent == name or fields[parent]['unit'] != rule['unit']):
            raise ValueError('invalid parent quantity relationship')
        seen = {name}
        while parent:
            if parent in seen:
                raise ValueError('cyclic mapped units')
            seen.add(parent)
            parent = fields[parent].get('included_in')
    return mapping


def normalize(payload, mapping):
    values, anomalies = {}, []
    for name, rule in mapping['fields'].items():
        found, raw = pointer(payload, rule['path'])
        item = {'value': None, 'unit': rule['unit'], 'path': rule['path'],
                'included_in': rule.get('included_in'), 'status': 'unknown',
                'reason': 'missing' if not found else 'null'}
        if rule.get('not_applicable'):
            item.update(status='not_applicable', reason='explicit mapping rule')
            if found and raw is not None:
                try:
                    if quantity(raw, integer=rule['unit'] != 'second') != 0:
                        anomalies.append(name + ': nonzero provider quantity contradicts not_applicable rule')
                except ValueError:
                    anomalies.append(name + ': invalid quantity contradicts not_applicable rule')
        elif found and raw is not None:
            try:
                value = quantity(raw, integer=rule['unit'] != 'second')
                item.update(value=str(value), status='reported_zero' if value == 0 else 'known', reason=None,
                            raw_type='number' if isinstance(raw, (Number, int, Decimal)) else 'string',
                            raw_lexeme=str(raw))
            except ValueError as exc:
                item.update(status='invalid', reason=str(exc))
                anomalies.append(name + ': invalid quantity')
        values[name] = item
    for name, item in values.items():
        parent = item['included_in']
        if parent and item['value'] is not None and values[parent]['value'] is not None:
            if Decimal(item['value']) > Decimal(values[parent]['value']):
                anomalies.append(name + ': exceeds parent ' + parent)
    if all(n in values and values[n]['value'] is not None and values[n]['unit'] == 'token' for n in ('input_tokens', 'output_tokens')):
        with localcontext() as ctx:
            ctx.prec = 4096
            derived = Decimal(values['input_tokens']['value']) + Decimal(values['output_tokens']['value'])
        total = values.get('total_tokens')
        if total and total['value'] is not None and Decimal(total['value']) != derived:
            anomalies.append('provider total differs from input + output')
        values['derived_total_tokens'] = {'value': str(derived), 'status': 'derived', 'unit': 'token',
                                          'included_in': None, 'reason': None}
    return {'fields': values, 'anomalies': anomalies}


def event_selection(events, mapping):
    """Deduplicate exporter resends, never deduplicate separate HTTP attempt IDs."""
    seen, seen_ids, selected, anomalies = {}, {}, [], []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('event object required')
        sequence = event.get('sequence')
        if type(sequence) is not int or sequence < 1:
            raise ValueError('event sequence must be a positive integer')
        producer = event.get('producer')
        if not isinstance(producer, str) or isinstance(producer, Number) or not producer:
            raise ValueError('event producer required')
        identity = (producer, sequence)
        digest = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        if identity in seen:
            if seen[identity] != digest:
                anomalies.append('conflicting exporter event identity')
            continue
        seen[identity] = digest
        eid = event.get('event_id')
        if eid is not None:
            if not isinstance(eid, str) or isinstance(eid, Number):
                raise ValueError('event_id must be a string or null')
            eid_key = (producer, eid)
            if eid_key in seen_ids and seen_ids[eid_key] != digest:
                anomalies.append('event ID repeated with different producer sequence/content')
            seen_ids[eid_key] = digest
        if event.get('kind') in mapping['usage_events']:
            selected.append(event)
    sources = {e['producer'] for e in selected}
    if len(sources) > 1:
        anomalies.append('multiple usage sources; no unique accounting basis')
    selected.sort(key=lambda e: e['sequence'])
    if not selected:
        return normalize({}, mapping), None, anomalies
    if mapping['event_semantics'] == 'cumulative':
        terminals = [e for e in selected if e.get('terminal') is True]
        if not terminals:
            anomalies.append('no terminal usage; partial cumulative observation')
            basis = selected[-1]
        else:
            # Identical repeated terminal values are a single accounting basis;
            # differing terminal values require an explicit correction relation.
            basis = terminals[0]
            for event in terminals[1:]:
                if event['payload'] != basis['payload']:
                    if event.get('supersedes') != basis.get('event_id') or not basis.get('event_id'):
                        anomalies.append('conflicting terminal usage')
                    else:
                        basis = event
        return normalize(basis['payload'], mapping), {
            'producer': basis['producer'], 'sequence': basis['sequence'],
            'event_id': basis.get('event_id'), 'semantics': 'cumulative'}, anomalies
    normalized = [normalize(e['payload'], mapping) for e in selected]
    combined = normalize({}, mapping)
    for name in mapping['fields']:
        items = [n['fields'][name] for n in normalized]
        if all(i['value'] is not None for i in items):
            with localcontext() as ctx:
                ctx.prec = 4096
                value = sum((Decimal(i['value']) for i in items), Decimal(0))
            combined['fields'][name].update(value=str(value), status='derived', reason='nonoverlapping mapped deltas')
    combined['anomalies'] = [a for n in normalized for a in n['anomalies']]
    return combined, {'semantics': 'incremental', 'events': [e.get('event_id') for e in selected]}, anomalies
