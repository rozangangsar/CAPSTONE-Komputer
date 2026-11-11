// Replay only changes the preview; capture and calibration remain server-owned.
const replayBar=document.createElement('div');
replayBar.className='replay-bar';
replayBar.innerHTML='<button class="button secondary" id="import-csv">Impor data</button><input id="csv-file" type="file" accept=".csv,text/csv" hidden><select id="csv-side" aria-label="Kaki untuk CSV satu kaki"><option value="left">CSV satu kaki: kiri</option><option value="right">CSV satu kaki: kanan</option></select><button class="button quiet" id="replay-play" hidden>Putar</button><input id="replay-seek" type="range" min="0" value="0" step="1" aria-label="Posisi replay" hidden><button class="button quiet" id="replay-close" hidden>Kembali live</button><span id="replay-status" role="status"></span>';
document.querySelector('#view-acquisition').prepend(replayBar);
let replayRows=null,replayIndex=0,replayPlaying=false,replayStart=0;
const liveApi=api;
api=async function(action,data={}) {
  if(replayRows&&['record/start','calibrate'].includes(action)){notice('Kembali live sebelum merekam atau kalibrasi.');return false;}
  return liveApi(action,data);
};
const originalMonitor=renderMonitor;
renderMonitor=function(){
  if(!replayRows)return originalMonitor();
  const saved=state,savedOnline=online;
  const row=replayRows[replayIndex], histories={left:[],right:[]},devices={};
  for(let i=replayIndex;i>=0&&row.t-replayRows[i].t<=10;i--)for(const foot of feet){
    const values=replayRows[i][foot]||Array(8).fill(null);histories[foot].unshift({t:replayRows[i].t,values,original:values});
  }
  for(const foot of feet)if(row[foot])devices[foot]={age_s:0,rate:'Replay',latest:{sensor_valid_mask:255}};
  state={running:true,devices,calibration:{history:histories,profiles:{pressure:{}}}};online=true;
  try{originalMonitor();feet.forEach(f=>{$(`status-${f}`).textContent=row[f]?'Replay CSV':'Tidak tersedia';});}
  finally{state=saved;online=savedOnline;}
};
function csvTable(text){
  const rows=[];let row=[],cell='',quoted=false;
  for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}
    else if(!quoted&&(c===','||c==='\n')){row.push(cell.replace(/\r$/,''));cell='';if(c==='\n'){if(row.some(Boolean))rows.push(row);row=[];}}
    else cell+=c;}
  if(quoted)throw Error('CSV memiliki kutip yang tidak ditutup');if(cell||row.length){row.push(cell);rows.push(row);}return rows;
}
$('import-csv').onclick=()=>{if(state?.recording)return notice('Hentikan rekaman sebelum membuka replay.');$('csv-file').click();};
$('csv-file').onchange=async event=>{
 try{
  const file=event.target.files[0];if(!file)return;if(file.size>30*1024*1024)throw Error('Batas CSV 30 MB.');
  const table=csvTable((await file.text()).replace(/^\uFEFF/,'')),header=table.shift();
  const combined=header?.includes('host_synchronized_timestamp_ns');
  const stamp=combined?'host_synchronized_timestamp_ns':header?.includes('host_rx_us')?'host_rx_us':'android_receive_timestamp_ns';
  if(!header?.includes(stamp))throw Error('Timestamp tidak dikenali. Gunakan CSV ekspor HP atau laptop.');
  const result=[];let first=null,previous=-Infinity;
  for(const cells of table){if(cells.length!==header.length)throw Error('Jumlah kolom CSV tidak konsisten.');const get=k=>cells[header.indexOf(k)];
   const t=Number(get(stamp))/(stamp.endsWith('_ns')?1e9:1e6);if(!get(stamp)?.trim()||!Number.isFinite(t)||t<0||t<previous)throw Error('Timestamp tidak valid atau mundur.');previous=t;first??=t;
   const item={t:t-first};for(const foot of feet){
    if(!combined&&foot!==(get('side')?.toLowerCase()||$('csv-side').value))continue;
    const keys=Array.from({length:8},(_,i)=>combined?`${foot}_fsr${i+1}`:header.includes('p1')?`p${i+1}`:`sensor${i+1}`);
    if(!keys.every(k=>header.includes(k)))throw Error('CSV harus memiliki delapan kanal sensor.');
    if(combined&&get(`${foot}_valid`)!=='true')continue;
    if(!combined&&header.includes('sensor_valid_mask')&&Number(get('sensor_valid_mask'))!==255)continue;
    if(!combined&&header.includes('sensor_data_valid')&&get('sensor_data_valid')==='false')continue;
    const values=keys.map(k=>get(k)===''?NaN:Number(get(k)));if(!values.every(v=>Number.isFinite(v)&&v>=0&&v<=65535))throw Error('Nilai sensor kosong atau di luar rentang.');item[foot]=values;
   }result.push(item);
  }
  if(!result.length||!result.some(r=>r.left||r.right))throw Error('CSV tidak memiliki sampel valid.');
  replayRows=result;replayIndex=0;replayPlaying=false;$('replay-seek').max=result.length-1;
  for(const id of ['replay-play','replay-seek','replay-close'])$(id).hidden=false;
  $('replay-status').textContent=`Replay · ${file.name} · ${result.length} sampel`;location.hash='acquisition';renderMonitor();
 }catch(e){notice(e.message);}finally{event.target.value='';}
};
$('replay-seek').oninput=e=>{replayIndex=Number(e.target.value);replayPlaying=false;renderMonitor();};
$('replay-play').onclick=()=>{if(replayIndex===replayRows.length-1)replayIndex=0;replayPlaying=!replayPlaying;replayStart=performance.now()/1000-replayRows[replayIndex].t;};
$('replay-close').onclick=()=>{replayRows=null;replayPlaying=false;for(const id of ['replay-play','replay-seek','replay-close'])$(id).hidden=true;$('replay-status').textContent='';if(state)renderMonitor();};
setInterval(()=>{if(!replayRows)return;if(replayPlaying){const t=performance.now()/1000-replayStart;while(replayIndex<replayRows.length-1&&replayRows[replayIndex+1].t<=t)replayIndex++;if(replayIndex===replayRows.length-1)replayPlaying=false;renderMonitor();}$('replay-seek').value=replayIndex;$('replay-play').textContent=replayPlaying?'Jeda':'Putar';},50);
