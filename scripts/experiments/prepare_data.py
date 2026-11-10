"""Prepare a reproducible public UCI snapshot outside the source checkout.

No training or fitting occurs. Archives are small, SHA-256 pinned, and downloaded
only from the UCI archive. This is source-version comparison, not truth proof.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import math
import random
import shlex
import statistics
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIGS = REPO / 'experiments/community/configs'
SOURCES = REPO / 'experiments/community/data/SOURCES.json'
FIELDS = ('id', 'source_row', 'group', 'x', 'y')
NAMES = ('selection_input', 'audit_reference', 'development_input', 'test_input', 'injection_truth', 'members')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def jsonbytes(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')

def canonical_digest(obj):
    return digest(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8'))

def load_config(task_id, seed):
    if task_id not in {f'EXP-{i:02d}' for i in range(1, 7)} or seed not in (1103, 2207, 3301):
        raise ValueError('unknown task or seed')
    config = json.loads((CONFIGS / task_id / f'{seed}.json').read_text(encoding='utf8'))
    if config['task_id'] != task_id or config['seed'] != seed:
        raise ValueError('configuration identity mismatch')
    return config

def get_archive(source, cache=None):
    if cache is not None:
        path = Path(cache) / (source['dataset'] + '.zip')
        blob = path.read_bytes()
    else:
        url = source['archive_url']
        if urllib.parse.urlparse(url).netloc != 'archive.ics.uci.edu' or not url.startswith('https://'):
            raise ValueError('untrusted source URL')
        request = urllib.request.Request(url, headers={'User-Agent': 'ResearchDatasetReproduction/1.0'})
        with urllib.request.urlopen(request, timeout=45) as response:
            if urllib.parse.urlparse(response.url).netloc != 'archive.ics.uci.edu' or not response.url.startswith('https://'):
                raise ValueError('untrusted source redirect')
            blob = response.read(source['download_limit_bytes'] + 1)
    if len(blob) > source['download_limit_bytes']:
        raise ValueError('archive size limit exceeded')
    if digest(blob) != source['archive_sha256']:
        raise ValueError('archive SHA-256 mismatch: ' + source['dataset'])
    return blob

def raw_rows(source, blob):
    name = source['dataset']
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if archive.testzip() is not None:
            raise ValueError('corrupt archive')
        info = archive.getinfo(source['member'])
        if info.file_size > 5_000_000:
            raise ValueError('uncompressed member limit exceeded')
        data = archive.read(info)
    if name == 'concrete':
        try:
            import xlrd
        except ImportError as exc:
            raise RuntimeError('Concrete requires xlrd==2.0.1; install the pinned experiment dependencies') from exc
        if xlrd.__version__ != '2.0.1':
            raise RuntimeError('Concrete requires xlrd==2.0.1')
        book = xlrd.open_workbook(file_contents=data)
        sheet = book.sheet_by_index(0)
        parsed = [[float(v) for v in sheet.row_values(i)] for i in range(1, sheet.nrows)]
        rows = [(r[:-1], r[-1]) for r in parsed]
    elif name == 'auto_mpg':
        parsed = [shlex.split(line) for line in data.decode('utf8').splitlines() if line.strip()]
        rows = [([None if v == '?' else float(v) for v in r[1:8]], float(r[0])) for r in parsed]
    elif name == 'wine_classification':
        parsed = [r for r in csv.reader(io.StringIO(data.decode('utf8'))) if r]
        rows = [([float(v) for v in r[1:]], r[0]) for r in parsed]
    elif name in ('spambase', 'ionosphere'):
        parsed = [r for r in csv.reader(io.StringIO(data.decode('utf8'))) if r]
        rows = [([float(v) for v in r[:-1]], r[-1]) for r in parsed]
    elif name == 'airfoil':
        parsed = [line.split() for line in data.decode('utf8').splitlines() if line.strip()]
        rows = [([float(v) for v in r[:-1]], float(r[-1])) for r in parsed]
    else:
        raise ValueError('unknown dataset')
    if len(rows) != source['rows'] or any(len(x) != source['features'] for x, _ in rows):
        raise ValueError('unexpected source shape')
    for x, y in rows:
        if any(v is not None and not math.isfinite(v) for v in x):
            raise ValueError('nonfinite feature')
        if isinstance(y, float) and not math.isfinite(y):
            raise ValueError('nonfinite target')
    return rows

def normalized_records(source, blob):
    pairs = raw_rows(source, blob)
    name = source['dataset']
    dim = source['features']
    axes = range(1, dim) if name == 'airfoil' else range(dim)
    low = {j: min(x[j] for x, _ in pairs if x[j] is not None) for j in axes}
    high = {j: max(x[j] for x, _ in pairs if x[j] is not None) for j in axes}
    rows = []
    for i, (x, y) in enumerate(pairs):
        bins = [None if x[j] is None else int(round((x[j]-low[j])/(high[j]-low[j] or 1)*1000)) for j in axes]
        group = digest(json.dumps(bins, separators=(',', ':')).encode())
        r = {'id': f'{name}:{i:05d}', 'source_row': i+1, 'group': group, 'x': x, 'y': y}
        r['member_sha256'] = canonical_digest(r)
        rows.append(r)
    return rows

def clean(row):
    return {k: copy.deepcopy(row[k]) for k in FIELDS}

def make_payloads(config, source, blob):
    original = normalized_records(source, blob)
    roles = config['roles']
    flat = [i for role in ('D0','pool','dev','test') for i in roles[role]]
    if len(flat) != len(original) or set(flat) != set(range(len(original))) or len(set(flat)) != len(flat):
        raise ValueError('invalid frozen member coverage')
    groups = [{original[i]['group'] for i in roles[key]} for key in ('D0','pool','dev','test')]
    if any(groups[i] & groups[j] for i in range(4) for j in range(i+1,4)):
        raise ValueError('group leakage across split')
    d0, pool = roles['D0'], roles['pool']
    seed, labels = config['seed'], config['allowed_labels']
    schema = {'feature_count': source['features'], 'feature_type': 'finite number or explicit missing',
              'label_type': 'classification string' if labels else 'regression number'}
    task = {'type': config['task_type'], 'feature_count': source['features'], 'allowed_labels': labels,
            'target': source['target'], 'evaluation_metric': config['primary_metric'],
            'target_model': config['target_model'], 'budget': {'added_candidates': config['k'], 'seed': seed}}
    asset = {'normalization_version':'ASSET-CANONICAL-1', 'dataset_id':source['dataset'],
             'dataset_version':'community-controlled-pool-v1', 'source_id':source['dataset_url'],
             'source_version':'uci-archive-pinned-v1', 'license_sha256':canonical_digest({'name':'CC BY 4.0','url':'https://creativecommons.org/licenses/by/4.0/'}),
             'schema_sha256':canonical_digest(schema), 'task_sha256':canonical_digest(task)}
    rows = [None] * len(original)
    for i in d0 + pool:
        rows[i] = clean(original[i])
    if labels and {rows[i]['y'] for i in d0} != set(labels):
        raise ValueError('frozen base training set lacks class')
    order = random.Random(seed + 51000).sample(pool, len(pool))
    n = int(.1 * len(pool))
    ys = [float(rows[i]['y']) for i in d0] if not labels else []
    delta = 2*(statistics.pstdev(ys) or 1.) if ys else None
    vals = [float(rows[i]['x'][0]) for i in d0 if rows[i]['x'][0] is not None]
    extreme = max(vals) + 3*(statistics.pstdev(vals) or 1.)
    truth = []
    for kind, indexes in [('label',order[:n]), ('feature',order[n:2*n])]:
        for i in indexes:
            before = clean(rows[i]); r = rows[i]
            if kind == 'label':
                r['y'] = labels[(labels.index(r['y'])+1) % len(labels)] if labels else float(r['y'])+delta
            else:
                r['x'][0] = extreme
            if (before['x'],before['y']) == (r['x'],r['y']):
                raise ValueError('controlled modification did not change row')
            truth.append({'index':i,'id':r['id'],'kind':kind,'before':before,'after':clean(r)})
    audit = random.Random(seed + 61000).sample(pool, min(64,len(pool)))
    references = [{'index':i,'id':original[i]['id'],'x':original[i]['x'],'y':original[i]['y']} for i in audit]
    members = {'roles': {k:[{'index':i,'id':original[i]['id'],'group':original[i]['group'],
                             'source_row':original[i]['source_row']} for i in indexes] for k,indexes in roles.items()},
               'audit_indices':audit, 'source_archive_sha256':source['archive_sha256'],
               'schema':schema, 'task':task, 'asset_metadata':asset,
               'seed_contract':{'perturbation':seed+51000,'audit':seed+61000,'shapley':seed+190000}}
    return {
        'selection_input':{'dataset':source['dataset'],'seed':seed,'asset_metadata':asset,'task':task,
                           'schema':schema,'rows':rows,'roles':{'D0':d0,'pool':pool}},
        'audit_reference':{'records':references,'scope':'source snapshot consistency, not factual oracle','selection':'uniform without replacement'},
        'development_input':{'records':[clean(original[i]) for i in roles['dev']]},
        'test_input':{'records':[clean(original[i]) for i in roles['test']]},
        'injection_truth':{'records':truth,'scope':'controlled source modifications, not verified natural wrong labels'},
        'members':members,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True, help='data root outside repository')
    parser.add_argument('--cache', type=Path, help='optional directory containing pinned <dataset>.zip')
    parser.add_argument('--dry-run', action='store_true', help='show plan without downloading or writing')
    args = parser.parse_args()
    cfg = load_config(args.task_id, args.seed)
    root = args.output.resolve()
    if root == REPO or REPO in root.parents:
        raise ValueError('output must be outside the repository')
    target = root / args.task_id / str(args.seed)
    if target.exists():
        raise FileExistsError('refusing to overwrite existing prepared input directory')
    all_sources = json.loads(SOURCES.read_text(encoding='utf8'))
    src = next(x for x in all_sources['datasets'] if x['dataset'] == cfg['dataset'])
    if args.dry_run:
        print(json.dumps({'status':'DRY_RUN','task_id':args.task_id,'seed':args.seed,'source':src['archive_url'],
                          'max_download_bytes':src['download_limit_bytes'],'target':str(target)},ensure_ascii=False))
        return
    blob = get_archive(src, args.cache)
    payloads = make_payloads(cfg, src, blob)
    encoded = {name:jsonbytes(value) for name,value in payloads.items()}
    pins = {item['path']:item['sha256'] for item in cfg['inputs']}
    for name, raw in encoded.items():
        rel = cfg[name]
        if rel != f'{args.task_id}/{args.seed}/{name}.json' or digest(raw) != pins.get(rel):
            raise ValueError('prepared input SHA-256 mismatch: ' + name)
    target.mkdir(parents=True, exist_ok=False)
    for name, raw in encoded.items():
        with (target / (name+'.json')).open('xb') as handle:
            handle.write(raw)
    print(json.dumps({'status':'PREPARED','task_id':args.task_id,'seed':args.seed,'files':len(encoded),
                      'archive_sha256':src['archive_sha256'],'output':str(target),'training':'NOT_RUN'},ensure_ascii=False))

if __name__ == '__main__':
    main()
