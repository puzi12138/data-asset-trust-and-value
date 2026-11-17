"""Check saved regression selections, models, and predictions; never fit."""
from pathlib import Path
import argparse,json,math,random,statistics,struct,sys
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from scripts.experiments import verify_result as ref
from scripts.external_experiments import verify as old
from scripts.external_experiments.regression_contract import validate_selection,validate_roles,validate_source_binding
from scripts.external_experiments.common import validate_config,planned_arms
read=ref.read_json;within=ref.path_in;digest=ref.digest;require=ref.require;number=ref.number;close=ref.close


def make_predictor(model,which,seed):
    p=model['params'];stats=model['preprocess_D0'];med=stats['median'];sc=stats['scale']
    require(len(med)==len(sc)>0 and all(number(x)>0 for x in sc),'Preprocess shape')
    if which=='ridge':
        require(model['model']=='Ridge' and p.get('alpha')==1 and p.get('fit_intercept') is True,'Ridge params')
        coef=model['coef'];intercept=number(model['intercept']);require(len(coef)==len(med),'Ridge dimensions')
        co=[number(x) for x in coef]
        def evaluate(z):return math.fsum(a*b for a,b in zip(co,z))+intercept
    else:
        require(model['model']=='RandomForestRegressor' and p.get('n_estimators')==100 and p.get('max_depth')==12 and p.get('random_state')==seed and p.get('n_jobs')==1,'Forest params')
        trees=model['trees'];require(len(trees)==100,'Forest size')
        for t in trees:require(len(t['children_left'])==len(t['children_right'])==len(t['feature'])==len(t['threshold'])==len(t['value'])>0,'Tree shape')
        def tree_predict(t,z):
            left=t['children_left'];right=t['children_right'];feature=t['feature'];threshold=t['threshold'];value=t['value'];i=0
            for _ in range(len(left)+1):
                require(type(i) is int and 0<=i<len(left),'Tree node index')
                if left[i]==-1:
                    require(right[i]==-1 and len(value[i])==1 and len(value[i][0])==1,'Tree leaf')
                    return number(value[i][0][0])
                j=feature[i];require(type(j) is int and 0<=j<len(z),'Tree feature index')
                i=left[i] if z[j]<=number(threshold[i]) else right[i]
            raise ref.InvalidResult('Tree cycle')
        def evaluate(z):
            f32=struct.unpack('<'+str(len(z))+'f',struct.pack('<'+str(len(z))+'f',*z))
            return statistics.fmean(tree_predict(t,f32) for t in trees)
    def predict(row):
        require(len(row['x'])==len(med),'Feature dimension')
        z=[((number(m) if x is None else number(x))-number(m))/number(s) for x,m,s in zip(row['x'],med,sc)]
        return evaluate(z)
    return predict


def metrics(ys,ps):
    require(len(ys)==len(ps)>=2,'Prediction count')
    errors=[a-b for a,b in zip(ys,ps)]
    mse=statistics.fmean(x*x for x in errors);average=statistics.fmean(ys)
    total=math.fsum((x-average)**2 for x in ys);res=math.fsum(x*x for x in errors)
    r2=(1.-res/total) if total else (1. if res==0. else 0.)
    return {'loss':statistics.fmean(abs(x) for x in errors),'rmse':math.sqrt(mse),'r2':r2}


def verify(result,config_path,data_dir):
    report={'status':'REJECTED','training_replayed':False,'scope':'Frozen inputs, selections, saved model arithmetic and predictions; not proof of factual truth or actual training.'}
    try:
        result=Path(result).resolve(strict=True);data_dir=Path(data_dir).resolve(strict=True);config_path=Path(config_path).resolve(strict=True)
        cfg=read(config_path);validate_config(cfg,data_dir)
        require(cfg['task_type']=='regression','Not regression')
        paths,source_path=old._inputs(cfg,data_dir)
        data=read(paths[cfg['selection_input']]);refs=read(paths[cfg['audit_reference']])['records']
        dev=read(paths[cfg['development_input']])['records'];test=read(paths[cfg['test_input']])['records'];source=read(source_path)
        rows,d0,pool,task,evidence,audit=validate_selection(data,refs,cfg)
        require(data['asset_metadata']['source_sha256']==source['raw_sha256'],'Source SHA binding')
        memberfile=[p for name,p in paths.items() if Path(name).name.lower()=='members.json'];require(len(memberfile)==1,'Members input')
        members=read(memberfile[0]);require(members['source_sha256']==source['raw_sha256'] and members['seed']==cfg['seed'],'Member binding')
        validate_source_binding(data,members,source,cfg)
        validate_roles(data,dev,test,cfg,members)
        fpath=within(result,'SELECTION_FREEZE.json');f=read(fpath);summary=read(within(result,'SUMMARY.json'));access=read(within(result,'HELDOUT_ACCESS.json'))
        require(f['config_sha256']==digest(config_path) and summary['selection_sha256']==digest(fpath)==access['selection_sha256'],'Selection binding')
        require(not f['test_labels_parsed_before_freeze'] and not f['development_labels_parsed_before_freeze'] and not f['injection_truth_parsed_before_freeze'],'Heldout ordering')
        require(number(access['heldout_read_started_monotonic'])>=number(f['frozen_monotonic']),'Heldout timestamp')
        require((summary['task_id'],summary['dataset'],summary['seed'])==(cfg['task_id'],cfg['dataset'],cfg['seed']),'Summary identity')
        require(summary['target_fit_attempts']==18 and summary['detector_fit_attempts']==1,'Fit attempts')
        stats=old._verify_selections(result,cfg,data,refs,f)
        detector=read(within(result,'RF_MODEL.json'))
        close(detector['calibration_label_delta'],2*(statistics.pstdev(float(rows[i]['y']) for i in d0) or 1.),'Label mutation delta')
        expected=set(planned_arms(cfg));arms=summary['methods'];by_arm={x['arm']:x for x in arms}
        require(len(arms)==18 and len(by_arm)==18 and set(by_arm)==expected,'Complete 18-arm matrix')
        counts=0;failures=[];losses={}
        for arm,m in by_arm.items():
            armroot=within(result,arm);stored=read(within(armroot,'METRICS.json'));require(stored==m,'Metrics mismatch')
            model_name=m['model'];method=m['method'];k=m['k']
            require(model_name in cfg['models'] and arm==model_name+'__'+('D0' if k==0 else 'k'+str(k)+'__'+method),'Arm identity')
            require(m['selection_sha256']==digest(fpath) and number(m['fit_started_monotonic'])>=number(access['heldout_read_started_monotonic']),'Fit timing')
            indices=d0+([] if k==0 else f['methods'][f'k{k}__{method}']['indices'])
            memberout=read(within(armroot,'TRAIN_MEMBERS.json'))
            require(memberout['indices']==indices and memberout['members']==[{key:rows[i][key] for key in ('id','source_row','group')} for i in indices],'Train member binding')
            require(m['n_train']==len(indices) and m['n_added']==k,'Training budget')
            if m['status']!='COMPLETE':failures.append(arm);continue
            model=read(within(armroot,'MODEL.json'));old._same_stats(stats,model['preprocess_D0']);predict=make_predictor(model,model_name,cfg['seed']);losses[arm]={}
            for split,records in (('dev',dev),('test',test)):
                saved=read(within(armroot,split+'_PREDICTIONS.json'));require(len(saved)==len(records)==m[split]['n'],'Prediction count')
                ys=[];ps=[]
                for r,p in zip(records,saved):
                    require((p['id'],p['group'],p['y'])==(r['id'],r['group'],r['y']),'Prediction membership')
                    y=number(r['y']);pred=predict(r);close(p['prediction'],pred,'Prediction',1e-7);close(p['absolute_error'],abs(y-pred),'Absolute error',1e-7)
                    ys.append(y);ps.append(pred);counts+=1
                recomputed=metrics(ys,ps)
                for key,value in recomputed.items():close(m[split][key],value,'Metric '+key,1e-7)
                losses[arm][split]=recomputed
        require(not failures and summary['status']=='COMPLETE','Failed/incomplete arms: '+','.join(failures))
        report.update(status='REPRODUCED',arms=len(arms),prediction_rows=counts,selectors_replayed=8,task_id=cfg['task_id'],seed=cfg['seed'],losses=losses)
    except (ref.InvalidResult,AssertionError,ValueError,TypeError,KeyError,IndexError,OverflowError,ZeroDivisionError,OSError,UnicodeError,StopIteration,struct.error) as exc:
        report['reason']=type(exc).__name__+': '+str(exc)
    return report


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--result',type=Path,required=True);ap.add_argument('--config',type=Path,required=True);ap.add_argument('--data-dir',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    out=a.output.resolve()
    for root in (a.result.resolve(),a.data_dir.resolve()):
        try:out.relative_to(root)
        except ValueError:pass
        else:raise ValueError('Output must be outside result and data roots')
    if out.exists() or out.is_symlink():raise FileExistsError('Refuse to overwrite')
    report=verify(a.result,a.config,a.data_dir);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf8') as f:json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report['status']=='REPRODUCED' else 1
if __name__=='__main__':sys.exit(main())
