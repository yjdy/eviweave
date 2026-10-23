"""Immutable imported evidence and recoverable, hash-chained derived generations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from contextlib import contextmanager
import fcntl

from experiment_store import atomic_bytes, fingerprint, internal, locked, read, sha, uid, write
from standalone import plain

KIND = 'eviweave-call-analysis'
CONTRACT = 'eviweave-model-call-observation'


def tool_identity():
    from standalone import PROJECT
    names = ('usage_normalize.py', 'observability_store.py', 'cost_import.py', 'cost_accounting.py',
             'cost_reports.py', 'cost_cli.py', 'observability_session.py', 'observability_probe.py')
    return {'version': 1, 'files': {name: sha(PROJECT / 'tools' / name) for name in names}}


def create(root, mapping, pricing=None, legacy=None):
    root = plain(root)
    if root.exists() and any(root.iterdir()):
        raise ValueError('analysis output must be empty; v1 experiments are never upgraded')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    write(root / 'resources/usage-mapping.json', mapping)
    if pricing:
        write(root / 'resources/pricing.json', pricing)
    doc = {'kind': KIND, 'schema_version': 2, 'import_identity_version': 2, 'contract': CONTRACT, 'contract_version': 1,
           'analysis_id': uid(), 'capture_level': 'task-summary-only' if legacy else 'raw-usage-import',
           'mapping_sha256': sha(root / 'resources/usage-mapping.json'),
           'initial_pricing_sha256': sha(root / 'resources/pricing.json') if pricing else None,
           'legacy': legacy, 'online_capture': False, 'enumeration_complete': False}
    doc['creation_tool_identity'] = tool_identity()
    write(root / 'observation.json', doc)
    return doc


def load(root):
    doc = read(root / 'observation.json')
    if doc.get('kind') != KIND or type(doc.get('schema_version')) is not int or doc['schema_version'] not in (1, 2) or doc.get('contract_version') != 1:
        raise ValueError('unsupported call-analysis contract')
    if doc['schema_version'] == 2 and (type(doc.get('import_identity_version')) is not int or doc['import_identity_version'] != 2):
        raise ValueError('unsupported import identity version')
    if sha(root / 'resources/usage-mapping.json') != doc['mapping_sha256']:
        raise RuntimeError('frozen mapping changed')
    if doc['initial_pricing_sha256'] and sha(root / 'resources/pricing.json') != doc['initial_pricing_sha256']:
        raise RuntimeError('frozen initial pricing changed')
    for member in root.rglob('*'):
        plain(member)
        if not member.is_file() and not member.is_dir():
            raise ValueError('non-regular analysis member')
    return doc


def require_writable(root):
    doc = load(root)
    if doc['schema_version'] == 1:
        raise ValueError('v1 analysis is read-only; import original evidence into a new v2 analysis')
    return doc


@contextmanager
def bundle_lock(root):
    """Old evidence uses an existing read-only shared lock; never create it."""
    if load(root)['schema_version'] != 1:
        with locked(root):
            yield
        return
    try:
        fd = os.open(plain(root / '.experiment.lock'), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise ValueError('v1 read-only analysis requires an existing lock file') from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('analysis is in use; retry after its writer finishes') from exc
        yield
    finally:
        os.close(fd)


def scan_journal(root):
    path = plain(root / 'api-calls/events.jsonl')
    if not path.exists():
        return [], []
    rows, gaps, previous = [], [], None
    for i, line in enumerate(path.read_bytes().splitlines(keepends=True), 1):
        if not line.endswith(b'\n'):
            gaps.append('journal incomplete final line; append refused until a new bundle is used')
            break
        try:
            row = json.loads(line)
            checksum = row.pop('sha256')
            if fingerprint(row) != checksum or row['sequence'] != i or row['previous_sha256'] != previous:
                raise ValueError('hash/sequence conflict')
            row['sha256'] = checksum
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError('journal corruption') from exc
        rows.append(row)
        previous = checksum
    checkpoint = root / 'api-calls/checkpoint.json'
    if checkpoint.exists():
        point = read(checkpoint)
        n = point['sequence']
        if type(n) is not int or n < 1 or n > len(rows) or rows[n - 1]['sha256'] != point['sha256']:
            raise RuntimeError('checkpoint conflicts with journal')
        if n != len(rows):
            gaps.append('durable checkpoint behind journal; recovered complete records')
    elif rows:
        gaps.append('checkpoint missing; imported records do not prove send durability')
    return rows, gaps


def append(root, event):
    require_writable(root)
    rows, gaps = scan_journal(root)
    if any('incomplete' in g for g in gaps):
        raise RuntimeError('cannot append after partial line; original evidence retained')
    row = dict(event, sequence=len(rows) + 1, previous_sha256=rows[-1]['sha256'] if rows else None)
    row['sha256'] = fingerprint(row)
    path = plain(root / 'api-calls/events.jsonl')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'ab') as handle:
            handle.write((json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n').encode())
            handle.flush()
            os.fsync(handle.fileno())
        write(root / 'api-calls/checkpoint.json', {'sequence': row['sequence'], 'sha256': row['sha256']})
    finally:
        # fd is owned/closed by the file object.
        pass
    return row


def register_import(root, sanitized, source_type, metadata):
    """Caller holds the exclusive bundle lock. Content-addressed import is idempotent."""
    require_writable(root)
    digest = hashlib.sha256(sanitized).hexdigest()
    identity = {'identity_version': 2, 'source_type': source_type, 'source_sha256': digest,
                'request_ids': sorted(metadata.get('request_ids', []))}
    iid = source_type + '-' + fingerprint(identity)
    relative = f'api-calls/imports/{iid}/source.bin'
    path = internal(root, relative)
    descriptor = dict(metadata, import_identity_version=2, import_id=iid, source_type=source_type, file=relative, sha256=digest)
    directory = path.parent
    if path.exists():
        if sha(path) != digest:
            raise RuntimeError('import evidence checksum changed')
        existing = directory / 'manifest.json'
        if existing.exists() and read(existing) != descriptor:
            raise ValueError('same evidence imported with different interpretation')
    else:
        atomic_bytes(path, sanitized)
    write(directory / 'manifest.json', descriptor)
    rows, _ = scan_journal(root)
    if not any(row.get('import_id') == iid for row in rows):
        append(root, {'event': 'evidence_imported', 'import_id': iid,
                      'manifest_sha256': sha(directory / 'manifest.json')})
    return descriptor


def imports(root):
    doc = load(root)
    rows, gaps = scan_journal(root)
    registered, result = set(), []
    for row in rows:
        iid = row.get('import_id')
        if not isinstance(iid, str) or not re.fullmatch(r'[a-z-]+-[0-9a-f]{64}', iid):
            raise RuntimeError('invalid journal import identity')
        if iid in registered:
            raise RuntimeError('duplicate journal import registration')
        registered.add(iid)
        directory = root / 'api-calls/imports' / iid
        manifest = read(directory / 'manifest.json')
        if sha(directory / 'manifest.json') != row['manifest_sha256'] or sha(internal(root, manifest['file'])) != manifest['sha256']:
            raise RuntimeError('import evidence/manifest damaged')
        expected = manifest['sha256'] if doc['schema_version'] == 1 else fingerprint({
            'identity_version': 2, 'source_type': manifest['source_type'], 'source_sha256': manifest['sha256'],
            'request_ids': sorted(manifest.get('request_ids', []))})
        if (manifest.get('import_id') != iid or iid != manifest['source_type'] + '-' + expected
                or (doc['schema_version'] == 2 and manifest.get('import_identity_version') != 2)):
            raise RuntimeError('import identity/physical request association damaged')
        result.append(manifest)
    for directory in (root / 'api-calls/imports').glob('*'):
        if directory.name not in registered:
            gaps.append('orphan import not accounted: ' + directory.name)
    return result, gaps


def check_generation(root, gid):
    """Validate a committed generation without writing, including legacy sources."""
    directory = root / 'api-calls/generations' / gid
    manifest_path = directory / 'manifest.json'
    if manifest_path.exists():
        manifest = read(manifest_path)
        if (manifest.get('evidence_generation_id') != gid or set(manifest.get('files', {})) != {'records.jsonl', 'billing.json', 'gaps.json'}
                or any(sha(plain(directory / name)) != digest for name, digest in manifest['files'].items())):
            raise RuntimeError('previous evidence generation damaged')
        return manifest
    return None


def generation(root, records, bills, gaps):
    if (root / 'observation.json').exists():
        require_writable(root)
    content = {'records': records, 'billing': bills, 'capture_gaps': gaps}
    gid = fingerprint(content)
    directory = root / 'api-calls/generations' / gid
    manifest_path = directory / 'manifest.json'
    manifest = check_generation(root, gid)
    if manifest is None:
        directory.mkdir(parents=True, exist_ok=True)
        atomic_bytes(directory / 'records.jsonl', b''.join((json.dumps(r, sort_keys=True, ensure_ascii=False) + '\n').encode() for r in records))
        write(directory / 'billing.json', bills)
        write(directory / 'gaps.json', gaps)
        manifest = {'kind': 'eviweave-call-generation', 'evidence_generation_id': gid,
                    'files': {n: sha(directory / n) for n in ('records.jsonl', 'billing.json', 'gaps.json')}}
        write(manifest_path, manifest)
    write(root / 'api-calls/manifest.json', manifest)
    return gid
