"""Versioned offline reports, conservative coverage and submission-cost diagnostics."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from decimal import Decimal, localcontext
import io
import json

from cost_accounting import calculate, currency_totals, validate_pricing
from cost_import import derive
from experiment_store import atomic_bytes, fingerprint, output_path, read, sha, uid, write
import observability_store as store
from standalone import plain

MEMBERS = ('summary.json', 'score.json', 'summary.csv', 'usage.json', 'cost.json',
           'calls.csv', 'cost-breakdown.csv', 'analysis.json')


def csv_bytes(rows, fields):
    handle = io.StringIO(newline='')
    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        safe = {}
        for name, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True, ensure_ascii=False)
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                value = "'" + value
            safe[name] = value
        writer.writerow(safe)
    return handle.getvalue().encode()


def usage_totals(records):
    fields = sorted({n for r in records for n in r['normalized_usage']['fields']})
    result = {}
    with localcontext() as ctx:
        ctx.prec = 4096
        for name in fields:
            items = [r['normalized_usage']['fields'].get(name, {'status': 'unknown', 'value': None}) for r in records]
            known = [i for i in items if i.get('value') is not None]
            result[name] = {'known_sum': str(sum((Decimal(i['value']) for i in known), Decimal(0))) if known else None,
                            'observed_with_value': len(known), 'observed_requests': len(items),
                            'statuses': dict(Counter(i['status'] for i in items)),
                            'coverage_scope': 'observed requests only; not whole experiment coverage'}
    return result


def billing_cost(record, bills):
    if record['source_conflict']:
        return {'amount': None, 'currency': None, 'complete': False, 'reason': 'conflicting request association'}
    rows = [b for b in bills if b['effective'] and b.get('request_attempt_id') == record['request_attempt_id']
            and b['grain'] == 'request' and b['amount_kind'] == 'actual_paid']
    if len(rows) == 1:
        row = rows[0]
        return {'amount': str(row['amount']), 'currency': row['currency'], 'complete': True,
                'bill_id': row['bill_id'], 'association_evidence': row['association_evidence'], 'reason': None}
    return {'amount': None, 'currency': None, 'complete': False,
            'reason': 'no uniquely associated actual-paid row' if len(rows) != 1 else 'unknown billing'}


def prepare_target(target, analysis_id):
    """Refuse foreign root copies; allow reconstruction from any committed version."""
    target = plain(target)
    known = {n: set() for n in MEMBERS}
    if (target / 'report-set.json').exists() and read(target / 'report-set.json').get('analysis_id') != analysis_id:
        raise ValueError('report destination belongs to another analysis')
    for path in (target / 'reports').glob('*/report-set.json'):
        manifest = read(path)
        if manifest.get('analysis_id') != analysis_id or set(manifest.get('files', {})) != set(MEMBERS):
            continue
        if any(sha(plain(path.parent / n)) != d for n, d in manifest['files'].items()):
            raise RuntimeError('committed report damaged; retained for audit')
        if manifest.get('pricing_file_sha256') and sha(plain(path.parent / 'pricing.json')) != manifest['pricing_file_sha256']:
            raise RuntimeError('committed pricing resource damaged')
        for name, digest in manifest['files'].items():
            known[name].add(digest)
    for name in MEMBERS:
        path = plain(target / name)
        if path.exists() and sha(path) not in known[name]:
            raise ValueError('refusing to overwrite unknown or modified report member: ' + name)


def report(root, output=None, pricing=None, revaluation=False):
    """Caller holds bundle lock, and output lock if output differs."""
    doc = store.load(root)
    legacy = doc['schema_version'] == 1
    if legacy:
        if output is None:
            raise ValueError('v1 analysis is read-only; --output must be a fresh separate directory')
        output = output_path(output, [root])
        # The CLI may already hold a newly created destination lock.
        if output.exists() and any(p.name != '.experiment.lock' for p in output.iterdir()):
            raise ValueError('v1 verification/revaluation output must be fresh')
        prepare_target(root, doc['analysis_id'])  # Validate old committed reports read-only.
    records, bills, gaps = derive(root)
    if legacy:
        store.check_generation(root, fingerprint({'records': records, 'billing': bills, 'capture_gaps': gaps}))
    if pricing is None and doc['initial_pricing_sha256']:
        pricing = read(root / 'resources/pricing.json')
    if pricing:
        validate_pricing(pricing)
    if revaluation and output is None:
        raise ValueError('revaluation requires a separate output directory')
    rid = uid()
    target = output or root
    generation_root = target / 'reports' / rid / 'evidence-generation' if legacy else root
    gid = store.generation(generation_root, records, bills, gaps)
    for record in records:
        record['public_list_calculated'] = calculate(record, pricing)
        record['billing_actual'] = billing_cost(record, bills)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    prepare_target(target, doc['analysis_id'])
    directory = target / 'reports' / rid
    directory.mkdir(parents=True, exist_ok=legacy)
    common = {'report_id': rid, 'evidence_generation_id': gid, 'analysis_id': doc['analysis_id']}
    usage = dict(common, kind='eviweave-call-usage', total_request_count=None,
                 observed_request_count=len(records), count_nature='lower_bound', fields=usage_totals(records),
                 unconfirmed_terminal=sum(not r['integrity']['terminal_observed'] for r in records),
                 unconfirmed_send=len(records), unattributed=sum(not all(r.get(n) for n in
                    ('experiment_id', 'plan_id', 'launch_id', 'task_id', 'task_attempt_id', 'interaction_id')) for r in records),
                 capture_level=doc['capture_level'], capture_gaps=gaps,
                 legacy_task_summary=doc.get('legacy'), legacy_is_not_added_to_request_totals=True)
    paths = ('provider_reported', 'public_list_calculated', 'billing_actual')
    totals = {name: currency_totals([r[name] for r in records]) for name in paths}
    groups = {}
    for dimension in ('task_attempt_id', 'task_id', 'plan_id', 'launch_id', 'experiment_id'):
        buckets = defaultdict(list)
        for record in records:
            buckets[record.get(dimension) or 'unattributed'].append(record)
        groups[dimension] = {key: {'observed_request_count': len(items),
                                 'usage': usage_totals(items),
                                 'costs': {name: currency_totals([r[name] for r in items]) for name in paths}}
                             for key, items in buckets.items()}
    effective_bills = [b for b in bills if b['effective']]
    unallocated = [b for b in effective_bills if b['grain'] != 'request'
                   or b.get('request_attempt_id') not in {r['request_attempt_id'] for r in records}
                   or b['amount_kind'] != 'actual_paid']
    reconciliation = []
    with localcontext() as ctx:
        ctx.prec = 4096
        for r in records:
            for other in ('provider_reported', 'billing_actual'):
                public, actual = r['public_list_calculated'], r[other]
                if public.get('amount') is not None and actual.get('amount') is not None and public.get('currency') == actual.get('currency'):
                    reconciliation.append({'request_attempt_id': r['request_attempt_id'], 'comparison': other + ' minus public_list_calculated',
                        'currency': public['currency'], 'difference': str(Decimal(actual['amount']) - Decimal(public['amount'])),
                        'explanation': 'unexplained; source periods, discounts, credits, taxes and rounding retained separately'})
    cost = dict(common, kind='eviweave-call-cost', complete=False,
                completeness_reason='request enumeration not certified for imported evidence',
                pricing_id=pricing['pricing_id'] if pricing else None,
                pricing_sha256=fingerprint(pricing) if pricing else None,
                valuation='revaluation' if revaluation else 'initial',
                paths=totals, groups=groups, billing_rows=bills, unallocated_billing_rows=unallocated,
                current_selection_costs=None,
                current_selection_reason='no certified executable experiment/selection snapshot in imported request bundle',
                reconciliation={'status': 'partial', 'allocation_policy': 'strong local request ID only; no proration',
                                'unexplained_differences': reconciliation},
                real_service_auth='NOT_TESTED', real_billing='NOT_TESTED')
    weighted = {}
    with localcontext() as ctx:
        ctx.prec = 4096
        for child, parent, name in [('cached_input_tokens', 'input_tokens', 'weighted_cache_hit_ratio'),
                                    ('reasoning_output_tokens', 'output_tokens', 'reasoning_ratio')]:
            pairs = [(Decimal(r['normalized_usage']['fields'][child]['value']), Decimal(r['normalized_usage']['fields'][parent]['value']))
                     for r in records if child in r['normalized_usage']['fields'] and parent in r['normalized_usage']['fields']
                     and all(r['normalized_usage']['fields'][n]['value'] is not None for n in (child, parent))
                     and r['normalized_usage']['fields'][child]['included_in'] == parent
                     and not r['normalized_usage']['anomalies']]
            denominator = sum((p for _, p in pairs), Decimal(0))
            weighted[name] = {'value': str(sum((c for c, _ in pairs), Decimal(0)) / denominator) if denominator else None,
                              'comparable_observed_requests': len(pairs), 'excluded_requests': len(records) - len(pairs)}
    analysis = dict(common, kind='eviweave-cost-analysis', ratios=weighted,
                    input_length_distribution=[r['normalized_usage']['fields'].get('input_tokens', {}).get('value') for r in records],
                    retry_requests=sum(bool(r['retry_of']) for r in records),
                    failed_requests=sum((r['http_status'] or 0) >= 400 or r['transport_status'] in ('failed', 'timeout', 'cancelled') for r in records),
                    comparison_policy='compare only matching units, pricing conditions and evidence scope; no automatic experiments')
    for label, subset in [('retries', [r for r in records if r['retry_of']]),
                          ('failed', [r for r in records if (r['http_status'] or 0) >= 400 or r['transport_status'] in ('failed', 'timeout', 'cancelled')])]:
        analysis[label + '_costs'] = {name: currency_totals([r[name] for r in subset]) for name in paths}
    issues = gaps + [r['request_attempt_id'] + ': ' + a for r in records for a in
                     (r['conflicts'] + r['normalized_usage']['anomalies'])]
    summary = dict(common, kind='eviweave-call-analysis-report', status='partial',
                   source_schema_version=doc['schema_version'],
                   integrity='FAIL' if issues else 'PASS', anomalies=issues,
                   capture_level=doc['capture_level'], online_capture=False, total_request_count=None,
                   observed_request_count=len(records), model_calls_during_verification=0,
                   cost_complete=False, original_experiment_mutated=False,
                   derivation_tool_identity=store.tool_identity(),
                   creation_tool_identity=doc['creation_tool_identity'],
                   legacy_task_summary=doc.get('legacy'))
    score = dict(common, kind='eviweave-cost-score-link', source='legacy frozen report snapshot' if doc.get('legacy') else None,
                 scores=doc.get('legacy', {}).get('scores') if doc.get('legacy') else None,
                 policy='cost analysis never changes prediction selection or scoring')
    for name, value in [('summary.json', summary), ('score.json', score), ('usage.json', usage),
                        ('cost.json', cost), ('analysis.json', analysis)]:
        write(directory / name, value)
    rows = [dict(r, **common) for r in records]
    fields = [*common, 'request_attempt_id', 'experiment_id', 'plan_id', 'launch_id', 'task_id', 'task_attempt_id',
              'interaction_id', 'retry_of', 'retry_index', 'http_status', 'transport_status', 'source_conflict',
              'normalized_usage', *paths, 'evidence']
    for name in ('calls.csv', 'summary.csv'):
        atomic_bytes(directory / name, csv_bytes(rows, fields))
    breakdown = [dict(common, request_attempt_id=r['request_attempt_id'], path=path,
                      **r[path]) for r in records for path in paths]
    atomic_bytes(directory / 'cost-breakdown.csv', csv_bytes(breakdown,
        [*common, 'request_attempt_id', 'path', 'amount', 'known_subtotal', 'currency', 'complete', 'reason', 'pricing_id', 'components']))
    if pricing:
        # The exact revaluation resource is part of the committed report set's metadata.
        write(directory / 'pricing.json', pricing)
    manifest = dict(common, kind='eviweave-call-report-set',
                    files={name: sha(directory / name) for name in MEMBERS},
                    evidence_generation={'location': 'report-output' if legacy else 'source-analysis',
                        'path': (generation_root / 'api-calls/generations' / gid).relative_to(target if legacy else root).as_posix()},
                    pricing_file_sha256=sha(directory / 'pricing.json') if pricing else None)
    write(directory / 'report-set.json', manifest)
    # Version is committed first; each latest copy is replace+fsync, the pointer
    # is committed last. Recovery accepts only members of a complete version.
    for name in MEMBERS:
        atomic_bytes(target / name, (directory / name).read_bytes())
    write(target / 'report-set.json', manifest)
    return summary


def submission_check(root):
    """Imported evidence is deliberately insufficient for a formal total_usd."""
    doc = store.load(root)
    records, _, gaps = derive(root)
    return {'eligible': False, 'total_usd': None, 'status': 'diagnostic_only',
            'reasons': ['410-task trace coverage, all failures/retries and predeclared selection must be certified',
                        'public pricing, audited FX and trace/metadata reconciliation required',
                        'imported request enumeration and execution provenance are not certified'],
            'observed_tasks': len({r['task_id'] for r in records if r['task_id']}),
            'capture_level': doc['capture_level'], 'gaps': gaps}
