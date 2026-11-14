"""Build bounded previews of original attachments before candidate generation."""

import csv
import json
from pathlib import Path
import re
import sqlite3
import subprocess


def question_terms(question):
    runs = re.findall(r'[\u3400-\u9fff]+|[a-z0-9_]+', question.lower())
    return {term for run in runs for term in (
        [run[i:i + 2] for i in range(len(run) - 1)] if '\u3400' <= run[0] <= '\u9fff' else [run]
    ) if len(term) > 1}


def score(text, terms):
    return len(terms.intersection(question_terms(text)))


def previews(blocks, terms):
    ranked = sorted(enumerate(blocks, 1), key=lambda block: -score(block[1], terms))
    return [(number, text.strip()) for number, text in ranked[:2] if text.strip()]


def pdf(path, terms):
    result = subprocess.run(['pdftotext', '-layout', str(path), '-'], capture_output=True,
                            text=True, errors='replace', timeout=20, check=True)
    selected = []
    for page, text in previews(result.stdout.split('\f'), terms):
        lines = text.splitlines()
        best = max(range(len(lines)), key=lambda i: score(lines[i], terms))
        selected.append({'physical_page': page, 'text': '\n'.join(lines[max(0, best - 2):best + 4])[:650]})
    return {'selected_text': selected, 'text_layer_empty': not result.stdout.strip()}


def markdown(path, terms):
    lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
    headings = [{'line': i, 'text': line[:140]} for i, line in enumerate(lines, 1) if line.startswith('#')]
    windows = ['\n'.join(lines[i:i + 4]) for i in range(0, len(lines), 4)]
    return {'headings': headings[:12], 'line_count': len(lines), 'excerpts': [
        {'start_line': (i - 1) * 4 + 1, 'text': text[:650]} for i, text in previews(windows, terms)
    ]}


def json_schema(path):
    with path.open(encoding='utf-8-sig', errors='replace') as handle:
        prefix = handle.read(65536)
    rows = re.search(r'"records"\s*:\s*\[\s*', prefix)
    if rows:
        try:
            row, _ = json.JSONDecoder().raw_decode(prefix, rows.end())
        except ValueError:
            return {'columns': []}
        if isinstance(row, dict):
            return {'columns': list(row)[:90]}
    return {'columns': []}


def sqlite_schema(path, terms):
    with sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        tables = [name for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        schemas = {name: [{'name': col, 'type': typ, **({'primary_key': True} if pk else {})}
                          for col, typ, pk in db.execute('SELECT name,type,pk FROM pragma_table_info(?)', (name,))]
                   for name in tables}
    relevant = sorted(tables, key=lambda n: -score(n + ' ' + ' '.join(c['name'] for c in schemas[n]), terms))
    return {'tables': tables, 'selected_schema': {n: schemas[n][:80] for n in relevant[:8]}}


def video(path, config, directory, index):
    jail = ['bwrap', '--unshare-net', '--ro-bind', str(config.workbench.rootfs_path), '/', '--dev', '/dev',
            '--proc', '/proc', '--tmpfs', '/mnt', '--ro-bind', str(path), '/mnt/source.mp4',
            '--bind', str(directory), '/mnt/output', '--']
    duration = float(subprocess.check_output(jail + ['/usr/bin/ffprobe', '-v', 'error', '-show_entries',
        'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', '/mnt/source.mp4'], text=True, timeout=15))
    frames = []
    for frame, fraction in enumerate((0.15, 0.38, 0.62, 0.85), 1):
        second = round(duration * fraction, 2)
        file = f'video-{index:02}-{frame}.jpg'
        subprocess.run(jail + ['/usr/bin/ffmpeg', '-nostdin', '-v', 'error', '-ss', str(second),
            '-i', '/mnt/source.mp4', '-frames:v', '1', '-vf', 'scale=1000:-2', '-threads', '1',
            f'/mnt/output/{file}'], check=True, capture_output=True, timeout=20)
        if not (directory / file).is_file():
            raise ValueError(f'未生成视频预览 {path.name} {second}s')
        frames.append({'seconds': second, 'file': file})
    return {'duration_seconds': duration, 'frames': frames}


def build(task, config, directory):
    directory.mkdir()
    terms = question_terms(task.question)
    originals = sorted(p for p in task.context_dir.rglob('*') if p.is_file())
    items = []
    for index, path in enumerate(originals, 1):
        entry = {'path': path.relative_to(task.context_dir).as_posix()}
        kind = path.suffix.casefold()
        try:
            if kind == '.pdf':
                entry.update(pdf(path, terms))
            elif kind == '.md':
                entry.update(markdown(path, terms))
            elif kind == '.csv':
                with path.open(encoding='utf-8-sig', errors='replace', newline='') as stream:
                    entry['columns'] = next(csv.reader(stream), [])[:90]
            elif kind == '.json':
                entry.update(json_schema(path))
            elif kind == '.sqlite':
                entry.update(sqlite_schema(path, terms))
            elif kind == '.mp4':
                entry.update(video(path, config, directory, index))
        except (ValueError, OSError, csv.Error, sqlite3.Error, subprocess.SubprocessError) as exc:
            entry['preview_unavailable'] = type(exc).__name__
        items.append(entry)
    document = {'sampling_note': 'Selected previews only. Samples are not a complete cohort or page set. '
                'PDF excerpts may omit tables or scans. Defer missing evidence to the full originals in stage two.',
                'originals': [item['path'] for item in items], 'previews': items}
    (directory / 'evidence.json').write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
