"""Select small, directly reproducible excerpts from the five pilot inputs."""

import csv
import json
import re
import sqlite3
import subprocess


VIDEO = {48: (16, 39, 51, 64), 103: (34, 50, 62, 78), 279: (20, 28, 40, 73)}
TABLES = {
    48: {'D_ICD_PROCEDURES': ('ICD9_CODE', 'SHORT_TITLE', 'LONG_TITLE')},
    103: {'A股增发': ('序号', '中文名称缩写', '预案公布日期', '增发目的')},
    129: {'lc_sharestru': ('CompanyCode', 'EndDate', 'NonListedShares', 'RestrictAShareP', 'Ashares', 'AFloats')},
    279: {'mf_bondportifoliodetail': ('InnerCode', 'BondCode', 'SecuAbbr')},
}


def excerpts(source, path, spans):
    lines = (source / path).read_text(encoding='utf-8').splitlines()
    return [{'path': path, 'lines': [start, end], 'text': '\n'.join(lines[start - 1:end])}
            for start, end in spans]


def database(source, number):
    path = source / 'db/sub_db.sqlite'
    with sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        tables = TABLES[number]
        schemas = {name: [dict(column=row[1], type=row[2], primary_key=bool(row[5]))
                          for row in db.execute(f'PRAGMA table_info("{name}")') if row[1] in columns]
                   for name, columns in tables.items()}
        examples = []
        if number == 103:
            query = 'SELECT 序号, 中文名称缩写, 预案公布日期 FROM "A股增发" WHERE substr(预案公布日期,1,4)=? ORDER BY 序号 LIMIT 3'
            examples = db.execute(query, ('2021',)).fetchall()
        if number == 129:
            query = ('SELECT CompanyCode, EndDate, NonListedShares, RestrictAShareP, Ashares, AFloats '
                     'FROM lc_sharestru WHERE {} LIMIT 2')
            for where in ('NonListedShares IS NOT NULL', 'RestrictAShareP > 0 AND NonListedShares IS NULL'):
                examples.append({'filter': where, 'rows': db.execute(query.format(where)).fetchall()})
    return {'path': 'db/sub_db.sqlite', 'selected_schema': schemas, 'samples': examples}


def json_sample(source, path, keys):
    doc = json.loads((source / path).read_text())
    return {'path': path, 'table': doc.get('table'), 'columns': list(doc['records'][0]),
            'samples': [{key: row.get(key) for key in keys} for row in doc['records'][:2]]}


def pdf_spans(source):
    path = 'doc/中国金融机构人民币信贷收支表.pdf'
    entries = []
    for page, anchors in ((4, ('档案 171', '记录 174', '档案 182')), (9, ('档案 171', '档案 174')), (10, ('档案 182',))):
        raw = subprocess.check_output(['pdftotext', '-f', str(page), '-l', str(page), '-layout',
                                       str(source / path), '-'], text=True, timeout=15)
        content = re.sub(r'\s+', ' ', raw)
        if '\ufffd' in content or not all(anchor in content for anchor in anchors):
            raise ValueError(f'第 {page} 页证据定位失败')
        entries.extend({'path': path, 'physical_page': page, 'text': content[pos:pos + 260]}
                       for anchor in anchors for pos in [content.index(anchor)])
    return entries


def build(task, number, config, directory):
    directory.mkdir()
    source = task.context_dir
    evidence = {'originals': sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())}
    if number in TABLES:
        evidence['database'] = database(source, number)
    if number == 35:
        evidence['pdf'] = pdf_spans(source)
    if number == 48:
        paths = ('csv/ADMISSIONS.csv', 'csv/PROCEDURES_ICD.csv')
        evidence['csv'] = {}
        for path in paths:
            with (source / path).open(newline='') as handle:
                evidence['csv'][path] = next(csv.reader(handle))
    if number == 129:
        evidence['text'] = excerpts(source, 'doc/lc_stockarchives.md', ((1, 5), (313, 319)))
        evidence['alternative'] = json_sample(source, 'json/lc_sharesfloatingschedule.json',
                                              ('CompanyCode', 'StartDateForFloating', 'NonMarketableAShares', 'TotalAShares'))
    if number == 279:
        evidence['manager'] = json_sample(source, 'json/mf_fundmanagernew.json', ())
        del evidence['manager']['samples']
    frames = []
    for second in VIDEO.get(number, ()):
        name = f'briefing-{second:03}.jpg'
        command = ['bwrap', '--unshare-net', '--ro-bind', str(config.workbench.rootfs_path), '/',
                   '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/mnt',
                   '--ro-bind', str(source / 'video/briefing.mp4'), '/mnt/briefing.mp4',
                   '--bind', str(directory), '/mnt/output', '--', '/usr/bin/ffmpeg', '-nostdin',
                   '-v', 'error', '-ss', str(second), '-i', '/mnt/briefing.mp4', '-frames:v', '1',
                   '-vf', 'scale=1280:-2', '-threads', '1', f'/mnt/output/{name}']
        subprocess.run(command, check=True, timeout=20)
        if not (directory / name).is_file():
            raise ValueError(f'缺少视频证据 {second}s')
        frames.append({'path': 'video/briefing.mp4', 'seconds': second, 'file': name})
    evidence['video'] = frames
    (directory / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
