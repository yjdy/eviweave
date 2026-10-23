"""Offline cost commands, separate from executable experiment formats."""
from __future__ import annotations

import json
from pathlib import Path

from cost_accounting import validate_pricing
from cost_import import import_source, local_source
from cost_reports import MEMBERS, report, submission_check
from experiment_store import (attempts, exclude_output, load as load_experiment, locked,
                              output_path, read, sha, write)
import observability_store as store
from standalone import PROJECT, plain
from usage_normalize import sanitize, parse, validate_mapping


def add_parser(sub):
    check = sub.add_parser('observe-check', help='loopback-only fixed-client diagnosis, no real model')
    check.add_argument('--observability-profile', type=Path)
    check.add_argument('--output', type=Path, required=True)
    cost = sub.add_parser('cost', help='explicit offline evidence import, verify and revalue')
    actions = cost.add_subparsers(dest='cost_command', required=True)
    imp = actions.add_parser('import')
    imp.add_argument('--experiment', type=Path, help='existing call-analysis bundle; legacy v1 only with --source-type legacy')
    imp.add_argument('--source', type=Path)
    imp.add_argument('--source-type', choices=('calls', 'responses-json', 'responses-sse', 'billing', 'legacy'), required=True)
    imp.add_argument('--metadata', type=Path, help='explicit association/identity of a single JSON/SSE request')
    imp.add_argument('--usage-mapping', type=Path)
    imp.add_argument('--pricing', type=Path)
    imp.add_argument('--output', type=Path, required=True, help='new bundle or independent import receipt')
    for action in ('verify', 'revalue', 'submission-check'):
        q = actions.add_parser(action)
        q.add_argument('--experiment', type=Path, required=True)
        q.add_argument('--output', type=Path)
        if action == 'revalue':
            q.add_argument('--pricing', type=Path, required=True)


def resource(path, validator):
    # Public templates and explicit local resources only; do not copy arbitrary
    # unrecognized secrets from profile/pricing documents into reports.
    raw = local_source(path)
    safe = sanitize(raw)
    return validator(json.loads(safe))


def legacy_import(experiment, output):
    if not plain(experiment / '.experiment.lock').is_file():
        raise ValueError('legacy source has no existing lock; refusing to create a file in the source')
    with locked(experiment):
        doc = load_experiment(experiment)
        rows = attempts(experiment, doc)
        summary = []
        for row in rows:
            run = experiment / row['attempt_id'] / 'run.json'
            result = read(run) if run.exists() else {}
            summary.append({'attempt_id': row['attempt_id'], 'task': row['task'], 'status': row['status'],
                            'usage': result.get('usage'), 'source_sha256': sha(run) if run.exists() else None})
        scores = None
        if (experiment / 'report-set.json').is_file():
            manifest = read(experiment / 'report-set.json')
            if manifest.get('experiment_identity') == doc['identity_sha256']:
                path = experiment / 'reports' / manifest['report_id'] / 'score.json'
                if path.is_file() and sha(path) == manifest['files']['score.json']:
                    scores = read(path)
        snapshot = {'experiment_identity': doc['identity_sha256'], 'attempts': summary, 'scores': scores,
                    'http_request_count': None, 'amount': None,
                    'reason': 'legacy task summaries cannot reconstruct physical HTTP requests or costs'}
        snapshot = json.loads(sanitize(json.dumps(snapshot, ensure_ascii=False).encode()))
        mapping = read(PROJECT / 'provenance/observability-mapping-v1.json')
        store.create(output, mapping, legacy=snapshot)
    with locked(output):
        return report(output)


def execute(args):
    if args.command == 'observe-check':
        if args.observability_profile:
            doc = resource(args.observability_profile, lambda d: d)
            if doc.get('kind') != 'eviweave-observability-profile':
                raise ValueError('invalid observation profile')
        from observability_probe import probe
        result = probe(args.output)
        print(json.dumps(result, ensure_ascii=False))
        return 0  # successful diagnosis, explicitly not a full-capture PASS
    root = output_path(args.experiment) if args.experiment else None
    source_doc = store.load(root) if root and args.cost_command != 'import' else None
    if args.cost_command == 'import':
        if root and (args.source_type != 'legacy' or (root / 'observation.json').exists()):
            store.require_writable(root)  # Reject before output creation or Git exclusion.
            if args.source_type == 'legacy':
                raise ValueError('legacy import requires a unified experiment, not a call-analysis bundle')
        protected = [p for p in (args.source, args.metadata, args.usage_mapping, args.pricing, root) if p]
        output = output_path(args.output, protected)
        if output.exists():
            raise ValueError('import output must be new; old evidence is retained')
        if args.source_type == 'legacy':
            if not root or any((args.source, args.metadata, args.usage_mapping, args.pricing)):
                raise ValueError('legacy import requires only --experiment and independent --output')
            if not output.is_relative_to(PROJECT / '.audit'):
                exclude_output(output)
            result = legacy_import(root, output)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if not args.source:
            raise ValueError('explicit --source required')
        metadata = json.loads(sanitize(local_source(args.metadata))) if args.metadata else None
        if root and any((args.pricing, args.usage_mapping)):
            raise ValueError('existing bundle resources are immutable; use revalue for another price')
        if not root:
            if args.source_type == 'billing':
                raise ValueError('billing import requires an existing analysis bundle')
            mapping = resource(args.usage_mapping or PROJECT / 'provenance/observability-mapping-v1.json', validate_mapping)
            pricing = resource(args.pricing, validate_pricing) if args.pricing else None
            # Validate all source semantics before creating the destination.
            from cost_import import prepare_source
            prepare_source(local_source(args.source), args.source_type, metadata)
            root = output
            if not root.is_relative_to(PROJECT / '.audit'):
                exclude_output(root)
            store.create(root, mapping, pricing)
        with locked(root):
            store.load(root)
            descriptor = import_source(root, args.source, args.source_type, metadata)
            result = report(root)
            if output != root:
                output.mkdir(parents=True, mode=0o700)
                if not output.is_relative_to(PROJECT / '.audit'):
                    exclude_output(output)
                write(output / 'import-receipt.json', {'import_id': descriptor['import_id'],
                      'source_sha256': descriptor['sha256'], 'report_id': result['report_id'], 'model_calls': 0})
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['integrity'] == 'PASS' else 1
    legacy = source_doc['schema_version'] == 1
    if args.cost_command == 'submission-check':
        with store.bundle_lock(root):
            result = submission_check(root)
        if args.output:
            target = output_path(args.output, [root])
            if target.exists():
                raise ValueError('submission diagnostic output must be new')
            target.mkdir(parents=True, mode=0o700)
            if not target.is_relative_to(PROJECT / '.audit'):
                exclude_output(target)
            write(target / 'submission-diagnostic.json', result)
        print(json.dumps(result, ensure_ascii=False))
        return 1
    target = output_path(args.output, [root]) if args.output else None
    if legacy and (not target or target.exists()):
        raise ValueError('v1 analysis is read-only; --output must be a fresh separate directory')
    if args.cost_command == 'revalue' and not target:
        raise ValueError('revalue requires a separate --output')
    pricing = resource(args.pricing, validate_pricing) if args.cost_command == 'revalue' else None
    with store.bundle_lock(root):
        if target:
            if target.exists() and any(p.name not in (*MEMBERS, 'reports', 'report-set.json', '.experiment.lock') for p in target.iterdir()):
                raise ValueError('foreign report target contents')
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not target.is_relative_to(PROJECT / '.audit'):
                exclude_output(target)
            with locked(target):
                result = report(root, target, pricing, revaluation=args.cost_command == 'revalue')
        else:
            result = report(root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['integrity'] == 'PASS' else 1
