"""Build small artificial Bike-shaped inputs; no model fitting or real data."""
from pathlib import Path
import argparse,copy,datetime,hashlib,json,math,random,statistics

NAMES=['season','yr','mnth','hr','holiday','weekday','workingday','weathersit','temp','atemp','hum','windspeed']
def encode(x):return (json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf8')
def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--seed',type=int,default=1103,choices=(1103,2207,3301));a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    rows=[];day0=datetime.date(2020,1,1)
    for day in range(105):
        date=day0+datetime.timedelta(days=day)
        for hour in range(24):
            x=[float(1+(day//27)%4),0.,float(date.month),float(hour),0.,float(date.weekday()),float(date.weekday()<5),float(1+day%3),float(.2+day/200),float(.21+day/200),float(.3+hour/100),float(.1+(day%7)/100)]
            y=20.+2*hour+3*x[8]+(day%7)
            i=len(rows)+1;group=date.isoformat();rows.append({'id':f'smoke:{i:05d}','source_row':i,'group':group,'split_group':group,'record_identity':f'{group}:{hour:02d}','x':x,'y':y})
    raw=encode(rows);source_sha=hashlib.sha256(raw).hexdigest();days=sorted({r['group'] for r in rows});n=len(days)-6;j=0;day_roles={}
    for role,count in [('D0',int(.20*n)),('buffer',2),('pool',int(.40*n)),('buffer',2),('dev',int(.15*n)),('buffer',2),('test',len(days))]:
        for d in days[j:j+count]:day_roles[d]=role
        j+=count
    allroles={x:[r for r in rows if day_roles[r['group']]==x] for x in ('D0','pool','dev','test','buffer')}
    selected=sorted(copy.deepcopy(allroles['D0']+allroles['pool']),key=lambda r:r['source_row']);d0=[i for i,r in enumerate(selected) if day_roles[r['group']]=='D0'];pool=[i for i,r in enumerate(selected) if day_roles[r['group']]=='pool']
    reference=[]
    for i in random.Random(a.seed+160000).sample(pool,256):
        r=selected[i];reference.append({'index':i,'id':r['id'],'x':r['x'][:],'y':r['y']})
    pick=random.Random(a.seed+170000).sample(pool,math.floor(.2*len(pool)));half=len(pick)//2;ydelta=2*(statistics.pstdev(selected[i]['y'] for i in d0) or 1.);xvals=[selected[i]['x'][8] for i in d0];xchanged=max(xvals)+3*(statistics.pstdev(xvals) or 1.)
    truth=[]
    for j,i in enumerate(pick):
        r=selected[i];kind='label' if j<half else 'feature';old=r['y'] if kind=='label' else r['x'][8]
        if kind=='label':r['y']+=ydelta
        else:r['x'][8]=xchanged
        truth.append({'index':i,'kind':kind,'original':old})
    data={'asset_metadata':{'source_sha256':source_sha},'task':{'type':'regression','feature_count':12,'feature_names':NAMES},'rows':selected,'roles':{'D0':d0,'pool':pool}}
    members={'source_sha256':source_sha,'seed':a.seed,'roles':{k:[{key:r[key] for key in ('source_row','id','group')} for r in v] for k,v in allroles.items()}}
    payload={'selection_input.json':data,'audit_reference.json':{'records':reference},'development_input.json':{'records':allroles['dev']},'test_input.json':{'records':allroles['test']},'injection_truth.json':{'records':truth},'MEMBERS.json':members,'source_record.json':{'dataset':'Synthetic Regression','scope':'SYNTHETIC_ONLY','raw_sha256':source_sha,'raw_size_bytes':len(raw)},'preparation.json':{'scope':'SYNTHETIC_ONLY','training':'NOT_RUN','role_counts':{k:len(v) for k,v in allroles.items()}}}
    inputs=[]
    for name,value in payload.items():
        rawbytes=encode(value);(out/name).write_bytes(rawbytes);inputs.append({'path':name,'sha256':hashlib.sha256(rawbytes).hexdigest()})
    cfg={'schema':'external-experiment-v1','task_id':'REGRESSION-SMOKE','scope':'SYNTHETIC_SMOKE_NON_SCIENTIFIC','dataset':'Synthetic Regression','seed':a.seed,'task_type':'regression','feature_count':12,'mutation_feature_index':8,'budgets':[128,512],'models':['ridge','random_forest'],'selection_input':'selection_input.json','audit_reference':'audit_reference.json','development_input':'development_input.json','test_input':'test_input.json','injection_truth':'injection_truth.json','inputs':inputs}
    (out/'config.json').write_bytes(encode(cfg));print(json.dumps({'status':'PREPARED','scope':'synthetic only, no training','role_counts':{k:len(v) for k,v in allroles.items()},'out':str(out)}))
if __name__=='__main__':main()
