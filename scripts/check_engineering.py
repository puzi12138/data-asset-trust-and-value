"""Run the current engineering checks without downloads or model training."""
from pathlib import Path
import argparse
import ast
import json
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
CORE = {
    'test_asset_binding', 'test_asset_decision', 'test_data_value',
    'test_dataset_quality', 'test_dataset_selection', 'test_evidence_policy',
    'test_task_authority', 'test_task_core', 'test_task_dimensions',
}
RESOURCES = {'test_training_supervisor'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['core', 'resources', 'web', 'all'], default='core')
    parser.add_argument('--receipt', type=Path, help='Optional new JSON output file; never overwritten.')
    args = parser.parse_args()
    if args.receipt and args.receipt.exists():
        parser.error('Receipt already exists; choose a new output path.')
    sources = sorted(path for folder in ['src', 'scripts', 'tests']
                     for path in (ROOT / folder).rglob('*.py')
                     if '__pycache__' not in path.parts)
    for path in sources:
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path.relative_to(ROOT)))
    available = {p.stem for p in (ROOT / 'tests').glob('test_*.py')}
    if not CORE | RESOURCES <= available:
        raise RuntimeError('Required engineering test module is missing.')
    selected = {'core': CORE, 'resources': RESOURCES,
                'web': available - CORE - RESOURCES, 'all': available}[args.suite]
    if not selected:
        raise RuntimeError('Requested suite has no tests.')
    start = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromNames(['tests.' + n for n in sorted(selected)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt = {'status': 'PASS' if result.wasSuccessful() else 'FAIL',
        'suite': args.suite, 'tests_run': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'python_syntax_files': len(sources),
        'elapsed_seconds': time.perf_counter() - start,
        'training': 'NOT_RUN', 'test_modules': sorted(selected)}
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.receipt.open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, indent=2)
            stream.write('\n')
    print(json.dumps(receipt))
    return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    raise SystemExit(main())
