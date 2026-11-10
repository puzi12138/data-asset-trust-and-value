"""Validate result-only PR payloads with this trusted checkout's verifier.

Never import code from the candidate checkout. Public input acquisition uses
this checkout's frozen registry. Missing trusted infrastructure fails closed.
"""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys

ROOT=Path(__file__).resolve().parents[2]

def fingerprint(folder):
    """Read data bytes only; unchanged archived runs need no repeat intake."""
    result=[];total=0
    for p in sorted(folder.rglob('*')):
        if p.is_symlink():raise ValueError('Symlink in result payload')
        if p.is_file():
            total+=p.stat().st_size
            if len(result)>=500 or total>50*1024*1024:raise ValueError('Oversized result payload')
            result.append((p.relative_to(folder).as_posix(),hashlib.sha256(p.read_bytes()).hexdigest()))
    return result

def main():
    a=argparse.ArgumentParser();a.add_argument('--candidate',required=True);a.add_argument('--baseline');a.add_argument('--data-root',required=True);a.add_argument('--output',required=True);o=a.parse_args()
    candidate=Path(o.candidate).resolve();base=candidate/'experiments/community/results/submissions'
    prior=Path(o.baseline).resolve()/'experiments/community/results/submissions' if o.baseline else None
    out=Path(o.output).resolve();out.mkdir(parents=True,exist_ok=False)
    if not base.exists():
        if prior and prior.exists() and any(prior.iterdir()):raise ValueError('Archived result deletion requires manual review')
        (out/'SUMMARY.json').write_text(json.dumps({'status':'NO_SUBMISSIONS','scientific_acceptance':False}),encoding='utf8');print('No result submissions.');return 0
    if base.is_symlink():raise ValueError('Symlink submission root')
    folders=sorted(base.iterdir())
    if any(not p.is_dir() or p.is_symlink() for p in folders):raise ValueError('Expected result directories only')
    if prior and prior.exists():
        if prior.is_symlink():raise ValueError('Symlink baseline root')
        if set(p.name for p in prior.iterdir())-set(p.name for p in folders):raise ValueError('Archived result deletion requires manual review')
        folders=[p for p in folders if not (prior/p.name).is_dir() or fingerprint(p)!=fingerprint(prior/p.name)]
    if len(folders)>4:raise ValueError('At most four new or changed result directories per PR; split larger submissions')
    records=[];prepared=set()
    for folder in folders:
        if not folder.is_dir() or folder.is_symlink():raise ValueError('Expected result directories only')
        folder.resolve().relative_to(base.resolve())
        manifest=folder/'RUN_MANIFEST.json'
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size>1_000_000:raise ValueError('Missing or oversized manifest')
        x=json.loads(manifest.read_text(encoding='utf8'));task=x.get('task_id');seed=x.get('seed')
        if task not in {f'EXP-{i:02}' for i in range(1,7)} or type(seed)!=int or seed not in (1103,2207,3301):raise ValueError('Unrecognized task/seed')
        report=out/f'{len(records):02}_VERIFY.json'
        if (task,seed) not in prepared:
            prep=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/experiments/prepare_data.py'),'--task-id',task,'--seed',str(seed),'--output',str(Path(o.data_root).resolve())],cwd=ROOT,capture_output=True,text=True,timeout=120)
            if prep.returncode:
                records.append({'task_id':task,'seed':seed,'status':'NEEDS_REVIEW','reason':'Trusted data preparation failed','exit':prep.returncode});continue
            prepared.add((task,seed))
        proc=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/experiments/verify_result.py'),'--result',str(folder),'--data-root',str(Path(o.data_root).resolve()),'--output',str(report)],cwd=ROOT,capture_output=True,text=True,timeout=180)
        detail=json.loads(report.read_text(encoding='utf8')) if report.exists() else {'status':'NEEDS_REVIEW'}
        records.append({'task_id':task,'seed':seed,'status':detail['status'],'exit':proc.returncode})
    ok=all(x['status']=='REPRODUCED' and x['exit']==0 for x in records)
    (out/'SUMMARY.json').write_text(json.dumps({'status':('REPRODUCED' if records else 'NO_SUBMISSIONS') if ok else 'NEEDS_REVIEW','scientific_acceptance':False,'results':records},indent=2),encoding='utf8')
    return int(not ok)

if __name__=='__main__':sys.exit(main())
