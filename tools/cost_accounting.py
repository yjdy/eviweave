"""Exact, conservative three-path cost accounting. No real rates are embedded."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, Inexact, localcontext, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_DOWN
import re

from usage_normalize import quantity

ROUNDINGS = {'half_even': ROUND_HALF_EVEN, 'half_up': ROUND_HALF_UP, 'truncate': ROUND_DOWN}
DIMENSIONS = ('provider', 'model', 'region', 'deployment', 'protocol', 'mode')
CALCULATION_POLICY_VERSION = 'decimal-quantum-v2'


def rounding_quantum(value):
    if value is None:
        return None
    if not isinstance(value, str) or quantity(value, integer=False) == 0:
        raise ValueError('rounding quantum must be a positive decimal string')
    return Decimal(value)


def round_amount(amount, quantum, mode):
    """Round a finite nonnegative Decimal to a multiple using exact integers.

    Decimal division followed by integer rounding can lose midpoint neighbors.
    Aligned coefficients keep that comparison exact, independent of context.
    """
    if quantum is None:
        return amount
    a, q = amount.as_tuple(), quantum.as_tuple()
    if not amount.is_finite() or amount < 0 or not quantum.is_finite() or quantum <= 0 or mode not in ROUNDINGS:
        raise ValueError('invalid monetary rounding operands')
    exponent = min(a.exponent, q.exponent)
    if max(len(a.digits), len(q.digits), abs(a.exponent - q.exponent)) > 10000:
        raise ValueError('monetary rounding exceeds supported precision')
    def coefficient(parts):
        value = 0
        for digit in parts.digits:
            value = value * 10 + digit
        return value * 10 ** (parts.exponent - exponent)
    numerator, denominator = coefficient(a), coefficient(q)
    multiple, remainder = divmod(numerator, denominator)
    if mode != 'truncate' and (2 * remainder > denominator or
            (2 * remainder == denominator and (mode == 'half_up' or multiple % 2))):
        multiple += 1
    # Construct directly: multiplying Decimals here would use the caller's context.
    value = multiple * denominator
    digits = []
    while value:
        value, digit = divmod(value, 10)
        digits.append(digit)
    return Decimal((0, tuple(reversed(digits)) or (0,), exponent))


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timezone-aware timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result


def validate_pricing(doc):
    if not isinstance(doc, dict) or doc.get('kind') != 'eviweave-pricing' or doc.get('schema_version') != 1 or not doc.get('pricing_id'):
        raise ValueError('unsupported pricing resource')
    if not doc.get('sources') or not doc.get('rule_version'):
        raise ValueError('pricing source and rule version required')
    for source in doc['sources']:
        if (not source.get('url', '').startswith('https://') or not re.fullmatch(r'[0-9a-f]{64}', source.get('sha256', ''))
                or not source.get('retrieved_at')):
            raise ValueError('pricing requires reviewed public source URL/time/SHA')
        timestamp(source['retrieved_at'])
    for rule in doc.get('prices', []):
        if any(not isinstance(rule.get(n), str) or not rule[n] for n in (*DIMENSIONS, 'currency')):
            raise ValueError('pricing conditions/currency must be explicit')
        if timestamp(rule['valid_from']) >= timestamp(rule['valid_until']):
            raise ValueError('invalid price validity interval')
        for component in rule['components']:
            if component.get('rate') is not None:
                if not isinstance(component['rate'], str):
                    raise ValueError('rates must be decimal strings')
                quantity(component['rate'], integer=False)
            if not isinstance(component.get('unit_scale'), str) or quantity(component['unit_scale'], integer=False) == 0:
                raise ValueError('positive decimal-string unit scale required')
        rounding = rule.get('rounding', {})
        if rounding.get('mode') not in ROUNDINGS:
            raise ValueError('rounding policy required')
        rounding_quantum(rounding.get('quantum'))
    return doc


def unknown(reason, pricing=None, components=None):
    return {'amount': None, 'known_subtotal': '0', 'currency': None, 'complete': False,
            'reason': reason, 'pricing_id': pricing, 'components': components or [], 'unpriced_units': [],
            'unrounded_amount': None, 'calculation_policy_version': CALCULATION_POLICY_VERSION}


def calculate(record, pricing):
    if not pricing:
        return unknown('no frozen pricing resource')
    ident = record['identity']
    if any(not ident.get(n) for n in DIMENSIONS) or not record.get('started_at'):
        return unknown('unknown applicability condition', pricing['pricing_id'])
    try:
        started = timestamp(record['started_at'])
    except ValueError:
        return unknown('unknown call time', pricing['pricing_id'])
    fields = record['normalized_usage']['fields']
    input_value = fields.get('input_tokens', {}).get('value')
    matches = []
    for rule in pricing['prices']:
        if any(rule[n] != ident[n] for n in DIMENSIONS):
            continue
        if not timestamp(rule['valid_from']) <= started < timestamp(rule['valid_until']):
            continue
        tier = rule.get('input_tier')
        if tier:
            if input_value is None:
                continue
            n = Decimal(input_value)
            if n < quantity(tier['min_inclusive']) or (tier.get('max_exclusive') is not None and n >= quantity(tier['max_exclusive'])):
                continue
        matches.append(rule)
    if len(matches) != 1:
        return unknown('price match must be unique: ' + str(len(matches)), pricing['pricing_id'])
    rule = matches[0]
    errors, details, subtotal = [], [], Decimal(0)
    recipes = {c['field']: c for c in rule['components']}
    if len(recipes) != len(rule['components']):
        errors.append('duplicate charge for the same unit')
    with localcontext() as ctx:
        ctx.prec = 4096
        ctx.clear_flags()
        for name, component in recipes.items():
            before_errors = len(errors)
            item = fields.get(name)
            if not item or item.get('unit') != component['unit']:
                errors.append('unmapped/unit mismatch: ' + name)
                continue
            if item.get('status') == 'not_applicable':
                errors.append('price charges not_applicable unit: ' + name)
                continue
            subtract = component.get('subtract', [])
            if len(subtract) != len(set(subtract)):
                errors.append('duplicate subtraction: ' + name)
                continue
            if item['value'] is None or component['rate'] is None:
                errors.append('unknown quantity or rate: ' + name)
                continue
            n = Decimal(item['value'])
            for child in subtract:
                child_item = fields.get(child, {})
                if child_item.get('included_in') != name or child_item.get('unit') != item['unit']:
                    errors.append('unproved subtraction relationship: ' + child)
                elif child_item.get('value') is None:
                    errors.append('unknown subtraction quantity: ' + child)
                else:
                    n -= Decimal(child_item['value'])
            if n < 0:
                errors.append('negative residual quantity: ' + name)
                continue
            if len(errors) != before_errors:
                continue
            amount = n * Decimal(component['rate']) / Decimal(component['unit_scale'])
            subtotal += amount
            details.append({'field': name, 'quantity': str(n), 'unit': component['unit'],
                            'rate': component['rate'], 'unit_scale': component['unit_scale'],
                            'subtract': subtract, 'amount': str(amount),
                            'formula': 'quantity * rate / unit_scale', 'source_path': item.get('path')})
        # Each applicable field has exactly one charge owner. Parent quantities
        # include reasoning/cache/modal children unless explicitly subtracted.
        unpriced = []
        for name, item in fields.items():
            if name in ('total_tokens', 'derived_total_tokens') or item['status'] == 'not_applicable':
                continue
            ancestors, cursor = [], name
            while cursor:
                ancestors.append(cursor)
                cursor = fields[cursor].get('included_in')
            owners = []
            for ancestor in ancestors:
                if ancestor not in recipes:
                    continue
                pos = ancestors.index(ancestor)
                below = ancestors[pos - 1] if pos else None
                if below not in recipes[ancestor].get('subtract', []):
                    owners.append(ancestor)
            if len(owners) != 1:
                errors.append(('overlapping' if owners else 'unpriced') + ' unit: ' + name)
                unpriced.append(name)
        if record['normalized_usage']['anomalies'] or record['source_conflict']:
            errors.append('source/usage conflict')
        quantum = rounding_quantum(rule['rounding'].get('quantum'))
        if quantum is not None and ctx.flags[Inexact]:
            raise ValueError('exact subtotal required for quantum rounding; supported precision exceeded')
        final = round_amount(subtotal, quantum, rule['rounding']['mode'])
    unsafe_subtotal = bool(record['normalized_usage']['anomalies'] or record['source_conflict'] or
                           any('overlapping' in error or 'duplicate charge' in error for error in errors))
    return {'amount': str(final) if not errors else None, 'known_subtotal': None if unsafe_subtotal else str(subtotal),
            'currency': rule['currency'], 'complete': not errors, 'reason': '; '.join(errors) if errors else None,
            'pricing_id': pricing['pricing_id'], 'components': details, 'unpriced_units': unpriced,
            'unrounded_amount': None if errors else str(subtotal),
            'calculation_policy_version': CALCULATION_POLICY_VERSION,
            'rounding': rule['rounding'], 'decimal_precision': 4096, 'source_conflict': record['source_conflict']}


def currency_totals(costs):
    currencies = sorted({c['currency'] for c in costs if c.get('currency')})
    result = {}
    with localcontext() as ctx:
        ctx.prec = 4096
        for currency in currencies:
            items = [c for c in costs if c.get('currency') == currency]
            n_unknown = sum(not c.get('complete') for c in items) + sum(not c.get('currency') for c in costs)
            subtotal = sum((Decimal(c.get('amount') or c.get('known_subtotal') or '0') for c in items), Decimal(0))
            result[currency] = {'known_subtotal': str(subtotal), 'unknown_calls': n_unknown,
                                'total': str(subtotal) if not n_unknown else None, 'complete': not n_unknown,
                                'unpriced_units': sorted({u for c in items for u in c.get('unpriced_units', [])})}
    return {'currencies': result, 'unknown_currency_calls': sum(not c.get('currency') for c in costs)}


def convert(amount, currency, fx):
    """Explicit one-way conversion, retained separately from all three cost paths."""
    if currency != fx.get('source_currency') or not fx.get('target_currency'):
        raise ValueError('exchange-rate direction mismatch')
    if not fx.get('source_url', '').startswith('https://') or not re.fullmatch(r'[0-9a-f]{64}', fx.get('source_sha256', '')):
        raise ValueError('auditable exchange-rate source required')
    timestamp(fx['date'])
    if not isinstance(fx.get('rate'), str):
        raise ValueError('decimal-string exchange rate required')
    with localcontext() as ctx:
        ctx.prec = 4096
        rate = quantity(fx['rate'], integer=False)
        if rate == 0:
            raise ValueError('exchange rate must be positive')
        return {'amount': str(quantity(amount, integer=False) * rate), 'currency': fx['target_currency'],
                'source_amount': amount, 'source_currency': currency, 'rate': fx['rate'],
                'direction': 'target units per source unit', 'source': fx, 'is_provider_reported': False}
