"""Read recorded samples for the existing live preview renderer."""
import csv
import json
import math
from pathlib import Path

def read_replay(folder):
    folder=Path(folder)
    metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    if metadata.get('status')!='complete':raise ValueError('Sesi belum tersimpan lengkap')
    rows=[]
    if metadata.get('input_format')=='public_pressure_100hz':
        from public_model import samples
        chunks,_=samples(folder/'source.txt')
        for times,values in chunks:
            for t,x in zip(times,values):
                for foot,offset in (('left',0),('right',14)):
                    pressure=(x[offset:offset+8]*100).tolist()
                    rows.append(dict(t=float(t),foot=foot,values=pressure,original=pressure,imu=(x[offset+8:offset+14]*1000).tolist(),mask=255,rate=100))
    else:
        for foot in ('left','right'):
            path=folder/f'{foot}.csv'
            if not path.exists():continue
            with path.open(encoding='utf-8-sig',newline='') as file:
                for row in csv.DictReader(file):
                    def number(key):
                        value=float(row[key])
                        return value if math.isfinite(value) else None
                    original=[number(f'p{i}') for i in range(1,9)]
                    processed=[number(f'filtered{i}') if row.get(f'filtered{i}') else original[i-1] for i in range(1,9)]
                    axes=('ax','ay','az','gx','gy','gz')
                    imu=[number(k) for k in axes] if all(row.get(k) for k in axes) else None
                    timestamp=number('host_rx_us')
                    if timestamp is None:raise ValueError('Timestamp tidak valid')
                    rows.append(dict(t=timestamp/1e6,foot=foot,values=processed,original=original,imu=imu,mask=int(row.get('sensor_valid_mask') or 255),rate=int(row.get('fs_hz') or 30)))
    if not rows:raise ValueError('Rekaman tidak memiliki sampel')
    rows.sort(key=lambda r:r['t']);start=rows[0]['t']
    for row in rows:row['t']-=start
    return rows
