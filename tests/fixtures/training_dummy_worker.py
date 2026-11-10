"""Harmless test-only worker; it performs no model or training computation."""
from pathlib import Path
import argparse,json,sys,time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/training'))
from guard import authorize
p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--out',required=True);a=p.parse_args()
config=json.loads(Path(a.config).read_text(encoding='utf8'))
if config['mode']=='forged':
 print(json.dumps({'kind':'TRAINING_READY_V1','nonce':'bad','worker_pid':0}),flush=True);time.sleep(8)
else:
 auth=authorize(a.config,a.out)
 if config['mode']=='ok':time.sleep(.15)
 else:time.sleep(30)
 print(json.dumps({'test_only':True,'authorized':auth['handshake']}),flush=True)
