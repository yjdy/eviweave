#!/usr/bin/env python3
"""Offline diagnostic process. No model client, network requests or expected files."""
import csv
import importlib.util
import json
from pathlib import Path

context = Path('/mnt/workspace')
rules = json.loads((context/'rules.json').read_text())
with (context/'records.csv').open(newline='', encoding='utf-8') as handle:
    records = list(csv.DictReader(handle))
selected = sorted((row for row in records if row['group'] in rules['groups']), key=lambda row: row['id'])
columns = ['id', 'note', 'value']
rows = [[row[column] for column in columns] for row in selected]
table = Path('/mnt/output/final_table.json')
table.write_text(json.dumps({'columns': columns, 'rows': rows}, ensure_ascii=False), encoding='utf-8')
spec = importlib.util.spec_from_file_location('frozen_exporter', '/mnt/runtime/dataspace-export.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
metadata = module.export_prediction(table)
Path('/mnt/output/export-metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
print(json.dumps({'type': 'synthetic.export', 'mode': 'synthetic/mock', 'model_calls': 0, 'export': metadata}))
