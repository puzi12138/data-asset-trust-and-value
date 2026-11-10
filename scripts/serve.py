"""Initialize an empty local workspace or serve a configured local workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


def prepare_workspace(path: Path, port: int) -> Path:
    """Create an empty workspace outside the source tree; never overwrite data."""
    if not 1024 <= port <= 65535:
        raise ValueError('PORT_MUST_BE_1024_TO_65535')
    workspace = path.resolve()
    if workspace.is_relative_to(ROOT.resolve()):
        raise ValueError('WORKSPACE_MUST_BE_OUTSIDE_SOURCE_TREE')
    workspace.mkdir(parents=True, exist_ok=False)
    from data_asset_poc.signed_delivery.security import generate_identities
    from data_asset_poc.dataspace.valuation import INDEX_VERSION

    identities = generate_identities(workspace / 'secrets')
    index = workspace / 'utility.sqlite'
    with sqlite3.connect(index) as db:
        db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, json TEXT NOT NULL)')
        db.execute('INSERT INTO metadata VALUES (?, ?)',
                   ('summary', json.dumps({'version': INDEX_VERSION, 'economics': [], 'legacy_rows': 0})))
        db.execute('CREATE TABLE utility (asset_id TEXT, version TEXT, content_hash TEXT, '
                   'task_id TEXT, economics_id TEXT, row_json TEXT, draws_json TEXT, '
                   'provenance_json TEXT, available_at TEXT)')
    manifest = workspace / 'workbench_manifest.json'
    manifest.write_text('{"cases": []}\n', encoding='utf-8')
    config = {'identity': identities['identities']['consumer'],
              'registry_url': 'http://127.0.0.1:9',
              'verifier_url': 'http://127.0.0.1:9',
              'provider_urls': {},
              'utility_index': str(index),
              'frontend_dist': str(ROOT / 'frontend' / 'dist'),
              'workspace': str(workspace),
              'demo_urls': {'consumer': f'http://127.0.0.1:{port}'},
              'task_id': '',
              'workbench_root': str(workspace),
              'workbench_manifest': {'path': str(manifest),
                  'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}}
    target = workspace / 'consumer.json'
    with target.open('x', encoding='utf-8') as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Create a new empty workspace outside the source tree')
    init.add_argument('--workspace', type=Path, required=True)
    init.add_argument('--port', type=int, default=12380)
    run = sub.add_parser('run', help='Serve an existing workspace on localhost')
    run.add_argument('--config', type=Path, required=True)
    run.add_argument('--port', type=int, default=12380)
    args = parser.parse_args()
    from scripts.check_environment import check
    environment = check()
    if environment['status'] != 'PASS':
        parser.error('Web dependencies are unavailable or incompatible: ' +
                     json.dumps(environment, ensure_ascii=False))
    if args.command == 'init':
        print('Created local configuration:', prepare_workspace(args.workspace, args.port))
        return
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be in 1024..65535')
    config = json.loads(args.config.read_text(encoding='utf-8'))
    if config.get('demo_urls', {}).get('consumer') != f'http://127.0.0.1:{args.port}':
        parser.error('Port must match the configured local consumer URL')
    # This default local entry point does not configure an external visual model.
    os.environ.pop('VISUAL_EVIDENCE_LIVE_VISUAL_ADAPTER', None)
    from data_asset_poc.numeric_validation.gateway import create_coverage_gateway
    import uvicorn
    uvicorn.run(create_coverage_gateway(args.config, project_root=ROOT), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
