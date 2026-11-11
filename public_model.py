"""100 Hz public pressure recordings; isolated from hardware-count inference."""
import json,uuid,time
from pathlib import Path
import numpy as np
REGIONS=((1,2),(5,6),(16,),(3,4),(9,),(14,),(13,),(7,8))
MODEL=Path(__file__).parent/'models/public_optimized.json'

def predict(window,model):
    a=np.asarray(window,dtype=np.float64).copy()
    if a.shape!=(200,28) or not np.isfinite(a).all():raise ValueError('Input model tidak valid')
    if model['config']['relative']:
        ix=list(range(8))+list(range(14,22));a[:,ix]/=max(.1,a[:,ix].sum(1).mean())
    a=np.clip((a-model['mean'])/model['std'],-8,8).T
    w=model['weights']
    for layer,dilation in ((0,1),(2,4),(4,16)):
        kernel=np.asarray(w[f'net.{layer}.weight']);padded=np.pad(a,((0,0),(4*dilation,4*dilation)))
        out=np.repeat(np.asarray(w[f'net.{layer}.bias'])[:,None],200,axis=1)
        for k in range(9):out+=kernel[:,:,k]@padded[:,k*dilation:k*dilation+200]
        a=np.maximum(0,out)
    pooled=a.reshape(16,4,50).mean(2).reshape(64)
    logits=np.asarray(w['net.9.weight'])@pooled+w['net.9.bias']
    return float(1/(1+np.exp(np.clip(logits[0]-logits[1],-700,700))))

def samples(path):
    rows=[]
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.startswith('#'):continue
        fields=line.split('\t' if '\t' in line else ',')
        try:float(fields[0])
        except ValueError:continue
        if len(fields)<51:raise ValueError('Kolom rekaman tidak lengkap')
        rows.append([float(v) if v.strip() else float('nan') for v in fields[:51]])
    if not rows:raise ValueError('Rekaman kosong')
    a=np.asarray(rows);t=a[:,0]
    if np.any(np.diff(t)<=0):raise ValueError('Timestamp tidak berurutan')
    x=np.concatenate([np.column_stack([a[:,[o+i for i in r]].mean(1) for r in REGIONS]+[a[:,o+i] for i in range(17,23)]) for o in (0,25)],1)
    chunks=[];start=0
    for i in range(len(t)+1):
        valid=i<len(t) and np.isfinite(x[i]).all()
        if i==len(t) or not valid or i>start and t[i]-t[i-1]>.015:
            if i-start>=30:
                xx=np.stack([np.convolve(x[start:i,c],np.ones(7)/7,'valid') for c in range(28)],1)
                chunks.append((t[start+3:i-3],xx))
            start=i if valid else i+1
    return chunks,len(rows)

def analyze(folder):
    folder=Path(folder);meta=json.loads((folder/'metadata.json').read_text());model=json.loads(MODEL.read_text())
    chunks,rows=samples(folder/'source.txt');timeline=[]
    origin=chunks[0][0][0] if chunks else 0
    for t,x in chunks:
        for i in range(0,len(x)-199,100):
            window=x[i:i+200]
            if max(np.ptp(window[:,0]),np.ptp(window[:,14]))<.5:continue
            started=time.perf_counter();p=predict(window,model)
            timeline.append(dict(start_s=float(t[i]-origin),end_s=float(t[i+199]-origin),label='ABNORMAL' if p>=model['decisionThreshold'] else 'NORMAL',abnormal_probability=p,inference_ms=(time.perf_counter()-started)*1000))
    average=sum(r['abnormal_probability'] for r in timeline)/len(timeline) if timeline else None
    label=('ABNORMAL' if average>=model['decisionThreshold'] else 'NORMAL') if timeline else None
    result=dict(status='complete' if timeline else 'insufficient_data',session=folder.name,subject=meta['subject'],label=meta['label'],result=label,abnormal_probability=average,confidence=(average if label=='ABNORMAL' else 1-average) if timeline else None,windows=len(timeline),rows=rows,discontinuities=max(0,len(chunks)-1),recording_seconds=float(chunks[-1][0][-1]-origin) if chunks else 0,message='Analisis selesai' if timeline else 'Data berjalan tidak cukup',model_version=model['name'],purpose=model['scope'],preprocessing='public_pressure_100hz',calibration_applied_to_model=False,weights_sha256=model['sha256'],timeline=timeline,analysis_id=uuid.uuid4().hex[:12])
    output=folder/'analyses'/result['analysis_id'];output.mkdir(parents=True);(output/'result.json').write_text(json.dumps(result,allow_nan=False))
    return result
