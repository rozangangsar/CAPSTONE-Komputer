const $ = (id) => document.getElementById(id);
const feet = ['left', 'right'];
const positions = [[.50,.89],[.59,.59],[.31,.21],[.40,.73],[.69,.31],[.63,.09],[.30,.36],[.39,.48]];
const regions = ['Tumit','Tengah telapak','Ujung depan luar','Tengah dekat tumit','Pangkal depan dalam','Ujung depan dalam','Pangkal depan luar','Tengah dekat depan'];
const n = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 1 });
const percent = new Intl.NumberFormat('id-ID', { style: 'percent', maximumFractionDigits: 1 });
const dates = new Intl.DateTimeFormat('id-ID', { day:'numeric', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' });
const esc = (text) => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = null, selected = 0, mode = 'processed', paused = false, busy = false, online = false;
let sessionReplay = null, replayRequest = 0;
let resultId = null, resultCache = null, currentView = '', firstState = true;
const path = 'M58.5 6C127.4-6 123.5 93 104 129C79.3 198 114.4 282 76.7 297C23.4 309 23.4 249 27.3 195C29.9 129 3.9 39 58.5 6Z';

for (const foot of feet) {
  const container = document.querySelector(`[data-foot="${foot}"]`);
  container.innerHTML = `<svg viewBox="0 0 130 310" aria-hidden="true"><defs><clipPath id="clip-${foot}"><path d="${path}" transform="${foot==='right'?'translate(130 0) scale(-1 1)':''}"/></clipPath><filter id="blur-${foot}" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="12"/></filter></defs><path d="${path}" transform="${foot==='right'?'translate(130 0) scale(-1 1)':''}" fill="#edf3fd" stroke="#b8cdeb" stroke-width="1.1"/><g clip-path="url(#clip-${foot})" filter="url(#blur-${foot})">${positions.map(([x,y],i)=>`<circle id="heat-${foot}-${i}" cx="${(foot==='right'?1-x:x)*130}" cy="${y*300}" r="29" fill="#2465e8" opacity="0"/>`).join('')}</g></svg>`;
  positions.forEach(([x,y],index) => {
    const button = document.createElement('button');
    button.type='button';button.className='sensor-point';button.id=`point-${foot}-${index}`;
    button.style.left=`${(foot==='right'?1-x:x)*100}%`;button.style.top=`${y*300/310*100}%`;
    button.textContent=index+1;button.setAttribute('aria-label',`${foot==='left'?'Kiri':'Kanan'}, kanal ${index+1}, ${regions[index]}`);
    button.addEventListener('click',()=>chooseChannel(index));container.append(button);
  });
}
for(let i=0;i<8;i++) {
  const button=document.createElement('button');button.type='button';button.textContent=i+1;
  button.setAttribute('aria-label',`Kanal ${i+1}: ${regions[i]}`);
  button.addEventListener('click',()=>chooseChannel(i));$('channel-picker').append(button);
}
function chooseChannel(index) {
  selected=index;$('channel-title').textContent=`Kanal ${index+1} · ${regions[index]}`;
  [...$('channel-picker').children].forEach((button,i)=>button.setAttribute('aria-pressed',String(i===selected)));
  feet.forEach(foot=>positions.forEach((_,i)=>$(`point-${foot}-${i}`).setAttribute('aria-pressed',String(i===selected))));
  if(state) renderMonitor();
}
chooseChannel(0);

async function api(action, data={}) {
  if(busy) return false;
  if(sessionReplay&&['record/start','calibrate'].includes(action))return false;
  if(action==='connect'||action==='disconnect')sessionReplay=null;
  busy=true;renderControls();
  try {
    const response=await fetch(`/api/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const body=await response.json();if(!response.ok) throw new Error(body.error || 'Permintaan gagal');
    notice('');await refresh();return true;
  } catch(error) { notice(error.message);return false; }
  finally {busy=false;renderControls();}
}
function notice(message) {$('notice').textContent=message;$('notice').hidden=!message;}
function isLive(foot) {const d=state?.devices?.[foot];return online && state.running && d && d.age_s<.5;}
function clockText(seconds) {const total=Math.max(0,Math.floor(seconds));return `${String(Math.floor(total/60)).padStart(2,'0')}:${String(total%60).padStart(2,'0')}`;}
function exportUrl(session) {return `/api/export/${encodeURIComponent(session)}`;}

$('connect').addEventListener('click',()=>api(state?.running?'disconnect':'connect',{port:Number($('udp-port').value)}));
$('cal-pressure').addEventListener('click',()=>api('calibrate',{kind:'pressure'}));
$('cal-imu').addEventListener('click',()=>api('calibrate',{kind:'imu'}));
$('record-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(state?.recording) {if(await api('record/stop')) location.hash='results';}
  else await api('record/start',{subject:$('subject').value,label:$('activity').value,notes:$('notes').value});
});
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{
  mode=button.dataset.mode;
  document.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  if(state) renderMonitor();
}));
document.querySelectorAll('[data-motion]').forEach(button=>button.addEventListener('click',()=>api('demo/motion',{motion:button.dataset.motion})));
$('pause-chart').addEventListener('click',()=>{paused=!paused;$('pause-chart').textContent=paused?'Lanjutkan grafik':'Jeda grafik';$('pause-chart').setAttribute('aria-pressed',String(paused));if(!paused&&state)renderMonitor();});
$('refresh-sessions').addEventListener('click',loadSessions);

function setView() {
  const requested=location.hash.slice(1);const view=['acquisition','results','sessions'].includes(requested)?requested:'acquisition';
  currentView=view;for(const item of ['acquisition','results','sessions']) $(`view-${item}`).hidden=item!==view;
  document.querySelectorAll('[data-view]').forEach(a=>{if(a.dataset.view===view)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  $('page-title').textContent={acquisition:'Live preview',results:'Hasil analisis',sessions:'Riwayat sesi'}[view];
  if(view==='sessions')loadSessions();
  if(state){if(view==='acquisition')renderMonitor();if(view==='results')renderResult();}
}
window.addEventListener('hashchange',setView);window.addEventListener('resize',()=>{if(state){renderMonitor();renderResult();}});
setView();

function renderControls() {
  if(!state)return;
  const live=feet.filter(isLive), capture=state.calibration.active;
  const analyzing=state.analysis.status==='running';
  $('connect').textContent=state.running?'Hentikan penerima':'Mulai menerima';
  $('connect').disabled=busy || !online || !!capture || state.recording || state.demo;
  $('udp-port').disabled=state.running;
  const fullSync=live.length===2&&feet.every(foot=>state.devices[foot].clock&&state.devices[foot].clock.rtt_us<=20000);
  $('connection').classList.toggle('live',live.length>0);
  $('connection').lastChild.textContent=!online?' Server terputus':fullSync?' Kedua insole tersinkron':live.length===2?' Menyinkronkan insole':live.length===1?' Satu insole aktif':state.running?' Menunggu insole':' Belum menerima';
  $('record').disabled=!!sessionReplay || busy || !online || !!capture || analyzing || (!state.recording&&!live.length);
  $('record').classList.toggle('recording',state.recording);
  $('record').innerHTML=state.recording?'<span aria-hidden="true">■</span> Stop & analisis':'<span aria-hidden="true">●</span> Mulai rekam';
  ['subject','activity','notes'].forEach(id=>$(id).disabled=state.recording||busy);
  $('record-time').textContent=clockText(state.elapsed);
  $('record-indicator').textContent=state.recording?'Merekam':analyzing?'Menganalisis':'Siap';
  $('record-counts').textContent=state.recording?`${n.format(state.counts.left||0)} kiri / ${n.format(state.counts.right||0)} kanan`:'Belum merekam';
  $('export-last').hidden=!state.path || state.recording || analyzing;
  $('export-last').href=state.path?exportUrl(state.path):'#';
  ['pressure','imu'].forEach(kind=>{
    $(`cal-${kind}`).disabled=!!sessionReplay||busy||!online||state.recording||!!capture||!live.length;
    const profiles=state.calibration.profiles[kind];
    const ready=live.filter(foot=>profiles[foot]);
    $(`cal-${kind}-state`).textContent=capture===kind?'Mengambil sampel…':ready.length?`${ready.map(f=>f==='left'?'Kiri':'Kanan').join(' & ')} tersimpan`:'Belum diambil';
  });
  $('calibration-progress').hidden=!capture;$('cal-progress').value=state.calibration.progress;
  $('cal-message').textContent=state.calibration.message || 'Ambil saat insole diam, tanpa beban.';
  $('demo-badge').hidden=!state.demo;$('demo-controls').hidden=!state.demo;
  document.querySelectorAll('[data-motion]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.motion===state.demo_motion));b.disabled=busy||!!capture;});
  $('hardware-detail').textContent=feet.filter(foot=>state.devices[foot]).map(foot=>{
    const d=state.devices[foot], history=state.calibration.history[foot], imu=history[history.length-1]?.imu;
    const axes=isLive(foot)&&d.latest.imu_valid!==false&&imu?`\nAccel: ${imu.slice(0,3).map(v=>n.format(v)).join(', ')} | Gyro: ${imu.slice(3).map(v=>n.format(v)).join(', ')} counts`:'\nIMU: tidak ada data segar/valid';
    return `${foot==='left'?'Kiri':'Kanan'}: ${d.device_id}${d.clock?`, RTT ${n.format(d.clock.rtt_us/1000)} ms`: ', sync belum tersedia'}${axes}`;
  }).join('\n\n');
  $('footer-state').textContent=state.demo?'Data simulasi untuk uji pipeline':`${state.stats.valid} paket · ${state.stats.gaps} celah urutan`;
}

function renderMonitor() {
  if(currentView!=='acquisition')return;
  for(const foot of feet) {
    const live=isLive(foot), d=state.devices[foot], mask=live?(d.latest.sensor_valid_mask??255):0;
    const points=state.calibration.history[foot];const last=points[points.length-1];
    const values=mode==='processed'?last?.values:last?.original;
    const count=Array.from({length:8},(_,i)=>Boolean(mask&(1<<i))).filter(Boolean).length;
    $(`status-${foot}`).textContent=live?(count===8?'Terhubung':`${count}/8 kanal valid`):'Menunggu';
    $(`status-${foot}`).className=`foot-status ${live?(count===8?'live':'invalid'):''}`;
    $(`detail-${foot}`).textContent=live?(state.calibration.profiles.pressure[foot]?'Baseline tersimpan':''):'Belum ada data';
    positions.forEach((_,i)=>{
      const valid=live&&Boolean(mask&(1<<i))&&values?.[i]!=null;
      const strength=valid?Math.max(0,Math.min(1,values[i]/1200)):0;
      $(`heat-${foot}-${i}`).setAttribute('opacity',valid?String(.06+strength*.9):'0');
      $(`heat-${foot}-${i}`).setAttribute('fill',strength>.75?'#14969c':'#2465e8');
      const point=$(`point-${foot}-${i}`);point.classList.toggle('invalid',!valid);
      point.title=`${regions[i]}: ${valid?n.format(values[i])+' counts':'Tidak ada data valid'}`;
      point.setAttribute('aria-label',`${foot==='left'?'Kiri':'Kanan'}, kanal ${i+1}, ${regions[i]}, ${valid?n.format(values[i])+' counts':'tidak ada data valid'}`);
    });
    $(`reading-${foot}`).textContent=live&&mask&(1<<selected)&&values?.[selected]!=null?n.format(values[selected]):'—';
  }
  if(!paused) {
    const series=feet.map(foot=>({color:foot==='left'?'#2465e8':'#117b75',points:isLive(foot)?state.calibration.history[foot].map(p=>[p.t,(mode==='processed'?p.values:p.original)?.[selected]]):[]}));
    const end=Math.max(0,...series.flatMap(s=>s.points.map(p=>p[0])));
    draw($('sensor-chart'),series,{minX:end-10,maxX:end});
    $('chart-empty').hidden=series.some(s=>s.points.some(p=>p[1]!=null));
  }
}

function draw(canvas,series,{minX=0,maxX=10,limit=null}={}) {
  const width=canvas.clientWidth,height=canvas.clientHeight;if(width<10)return;
  const ratio=window.devicePixelRatio||1;canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
  const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);
  const left=39,right=12,top=15,bottom=25,w=width-left-right,h=height-top-bottom;
  const values=series.flatMap(s=>s.points.filter(p=>p[0]>=minX&&p[0]<=maxX&&p[1]!=null).map(p=>p[1]));
  let low=limit?limit[0]:Math.min(0,...values),high=limit?limit[1]:Math.max(200,...values)*1.08;
  ctx.font='9px Jakarta, sans-serif';ctx.fillStyle='#61738a';ctx.lineWidth=1;
  for(let i=0;i<4;i++) {
    const y=top+h*i/3;ctx.beginPath();ctx.strokeStyle='#e8eef6';ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();
    ctx.textAlign='right';ctx.fillText(limit?percent.format(high-(high-low)*i/3):n.format(high-(high-low)*i/3),left-9,y+3);
  }
  for(let i=0;i<5;i++) {ctx.textAlign='center';const x=minX+(maxX-minX)*i/4;ctx.fillText(`${Math.round(limit?x:x-maxX)} s`,left+w*i/4,height-5);}
  for(const s of series) {
    ctx.beginPath();ctx.strokeStyle=s.color;ctx.lineWidth=2;ctx.lineJoin='round';ctx.lineCap='round';let previous=null;
    for(const [t,v] of s.points) {
      if(v==null||t<minX||t>maxX){previous=null;continue;}
      const x=left+(t-minX)/Math.max(.001,maxX-minX)*w,y=top+h-(v-low)/(high-low)*h;
      if(previous!=null&&t-previous<(limit?2:.2))ctx.lineTo(x,y);else ctx.moveTo(x,y);previous=t;
    }
    ctx.stroke();
  }
}

function renderResult() {
  const analysis=state.analysis, result=analysis.result;
  $('result-loading').hidden=analysis.status!=='running';
  $('result-empty').hidden=analysis.status==='running'||!!result;
  $('result-content').hidden=!result;
  if(analysis.status==='error')notice(`Rekaman tersimpan, tetapi analisis belum berhasil: ${analysis.error}`);
  if(!result)return;
  if(resultId!==result.analysis_id) {
    resultId=result.analysis_id;
    $('result-label').textContent=result.result==='NORMAL'?'Normal':result.result==='ABNORMAL'?'Abnormal':'Data belum cukup';
    $('result-message').textContent=result.result?`${result.subject} · ${result.session}`:result.message;
    $('result-windows').textContent=n.format(result.windows);
    $('result-duration').textContent=clockText(result.recording_seconds);
    $('result-score').textContent=result.abnormal_probability==null?'—':percent.format(result.abnormal_probability);
    $('result-export').href=exportUrl(result.session);
    $('model-detail').textContent='';
    $('window-rows').innerHTML=result.timeline.slice(0,200).map(p=>`<tr><td>${n.format(p.start_s)}–${n.format(p.end_s)} s</td><td>${p.label==='NORMAL'?'Normal':'Abnormal'}</td><td>${percent.format(p.abnormal_probability)}</td></tr>`).join('');
    $('window-limit').hidden=result.timeline.length<=200;
  }
  if(currentView==='results')draw($('result-chart'),[{color:'#2465e8',points:result.timeline.map(p=>[p.end_s,p.abnormal_probability])}],{minX:0,maxX:Math.max(1,result.recording_seconds),limit:[0,1]});
}

async function loadSessions() {
  try {
    const response=await fetch('/api/sessions');if(!response.ok)throw new Error('Riwayat belum dapat dibuka');
    const sessions=await response.json();$('sessions-empty').hidden=!!sessions.length;
    $('session-rows').innerHTML=sessions.map(s=>`<tr><td><strong>${esc(s.subject)}</strong><small>${esc(s.id)}</small></td><td>${esc(s.label)}</td><td>${esc(dates.format(new Date(s.started)))}</td><td>${n.format(s.counts.left||0)} / ${n.format(s.counts.right||0)}</td><td><div class="table-actions">${s.status==='complete'?`<button type="button" class="button secondary" data-analyze="${esc(s.id)}">Analisis</button><button type="button" class="button secondary" data-replay="${esc(s.id)}">Replay</button><a class="button quiet" href="${exportUrl(s.id)}">Unduh</a>`:`<span class="muted small">${s.status==='recording'?'Merekam':'Sesi parsial'}</span>`}</div></td></tr>`).join('');
    document.querySelectorAll('[data-replay]').forEach(b=>b.addEventListener('click',()=>startSessionReplay(b.dataset.replay)));
    document.querySelectorAll('[data-analyze]').forEach(b=>b.addEventListener('click',async()=>{if(await api('analyze',{session:b.dataset.analyze}))location.hash='results';}));
  } catch(error){notice(error.message);}
}

async function refresh() {
  try {
    const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw new Error('Server belum merespons');
    state=await response.json();online=true;
    if(state.analysis.result) {
      if(resultCache?.analysis_id!==state.analysis.result.analysis_id) {
        const resultResponse=await fetch('/api/result',{cache:'no-store'});
        if(!resultResponse.ok)throw new Error('Hasil belum dapat dimuat');
        resultCache=await resultResponse.json();
      }
      state.analysis.result=resultCache?.analysis_id===state.analysis.result.analysis_id?resultCache:null;
    }
    if(firstState&&state.demo){$('subject').value='DEMO-S001';firstState=false;}
    if(state.error)notice(state.error);
    applySessionReplay();renderControls();renderMonitor();renderResult();
  } catch(error) {
    online=false;notice('Koneksi aplikasi lokal terputus. Periksa terminal server sebelum melanjutkan.');
    renderControls();if(state)renderMonitor();
  }
}
window.addEventListener('beforeunload',event=>{if(state?.recording){event.preventDefault();event.returnValue='';}});
(async()=>{while(true){await refresh();await new Promise(resolve=>setTimeout(resolve,document.hidden?1000:200));}})();

async function startSessionReplay(session) {
  if(state?.recording||state?.calibration.active)return;
  const request=++replayRequest;
  try {
    const response=await fetch(`/api/replay/${encodeURIComponent(session)}`);
    const rows=await response.json();if(!response.ok)throw Error(rows.error);
    if(request!==replayRequest)return;
    sessionReplay={rows,index:0,start:performance.now(),history:{left:[],right:[]},devices:{}};
    paused=false;$('pause-chart').textContent='Jeda grafik';$('pause-chart').setAttribute('aria-pressed','false');
    notice('');location.hash='acquisition';await refresh();
  }catch(error){notice(error.message);}
}
function applySessionReplay() {
  if(!sessionReplay)return;
  const replay=sessionReplay,elapsed=(performance.now()-replay.start)/1000;
  if(elapsed>replay.rows.at(-1).t+.5){sessionReplay=null;return;}
  while(replay.index<replay.rows.length&&replay.rows[replay.index].t<=elapsed){
    const row=replay.rows[replay.index++],history=replay.history[row.foot];
    history.push({t:row.t,values:row.values,original:row.original,imu:row.imu});
    replay.devices[row.foot]={device_id:row.foot,rate:row.rate,sampleTime:row.t,latest:{sensor_valid_mask:row.mask,imu_valid:!!row.imu}};
  }
  for(const foot of feet){
    const history=replay.history[foot];while(history.length&&history[0].t<elapsed-10)history.shift();
    if(replay.devices[foot])replay.devices[foot].age_s=elapsed-replay.devices[foot].sampleTime;
  }
  state={...state,running:true,devices:replay.devices,calibration:{...state.calibration,history:replay.history,profiles:{pressure:{},imu:{}}}};
}
