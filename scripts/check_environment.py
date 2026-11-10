"""Inspect local Web dependencies without installing packages or using a network."""
from __future__ import annotations

import importlib.metadata
import json
import sys

REQUIRED = ('fastapi', 'uvicorn', 'pydantic', 'numpy', 'pandas', 'scipy',
            'scikit-learn', 'threadpoolctl', 'cryptography', 'Pillow')


def check():
    versions = {}
    for name in REQUIRED:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    missing = [name for name, version in versions.items() if version is None]
    compatible = versions['pydantic'] is not None and versions['pydantic'].split('.')[0] == '1'
    return {'status': 'PASS' if sys.version_info >= (3, 10) and not missing and compatible else 'FAIL',
            'python': sys.version.split()[0], 'versions': versions, 'missing': missing,
            'pydantic_major_1': compatible}


if __name__ == '__main__':
    result = check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
