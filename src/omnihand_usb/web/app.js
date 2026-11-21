import { TactilePanel } from "/tactile-view.js";
import { MotionPanel } from "/motion-panel.js";
const regions = ["THUMB", "INDEX", "MIDDLE", "RING", "LITTLE", "PALM", "DORSUM"];
const regionNames = ["拇指", "食指", "中指", "无名指", "小指", "手心", "手背"];
const jointNames = ["拇指旋转", "拇指侧摆", "拇指屈伸", "食指侧摆", "食指屈伸", "中指屈伸", "无名指侧摆", "无名指屈伸", "小指侧摆", "小指屈伸"];
const errorNames = {stalled:"堵转", overheat:"过热", over_current:"过流", motor_except:"电机异常", commu_except:"通信异常"};
const $ = id => document.getElementById(id);
let state = {connected:false, recording:false}, busy = false, lastSequence = -1, lastPositionTime = null, history = [], seenPort = null, actionError = null, seenMode = null;
let lastFullRender = 0;
const tactilePanel = new TactilePanel(()=>render(state));
const motionPanel = new MotionPanel(api,async()=>render(await api('/api/state')));

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache:"no-store"} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function notice(message) { $("notice").textContent = message || ""; $("notice").hidden = !message; }
function age(iso) { return iso ? Math.max(0, (Date.now() - Date.parse(iso)) / 1000) : Infinity; }
function valueLabel(id, value, unit) { const node=$(id); node.replaceChildren(document.createTextNode(value === null || value === undefined ? "--" : String(value))); const small=document.createElement("small"); small.textContent=unit; node.append(small); }

function initializeRows() {
  for (let i=0;i<10;i++) {
    const tr=document.createElement("tr");
    tr.innerHTML='<td><div class="joint-name"><span class="joint-index"></span><span class="name"></span></div></td><td><div class="position-cell"><span class="position-value">--</span><div class="bar-track"><div class="bar-fill"></div></div></div></td><td class="angle">--</td><td class="current">--</td><td class="temp">--</td><td><span class="status-label">--</span></td>';
    tr.querySelector(".joint-index").textContent=String(i+1).padStart(2,"0");
    tr.querySelector(".name").textContent=jointNames[i];
    $("joint-rows").append(tr);
    const opt=document.createElement("option");opt.value=i;opt.textContent=`${String(i+1).padStart(2,"0")} · ${jointNames[i]}`;$("chart-joint").append(opt);
  }

}

async function refreshPorts() {
  const old=$("port-select").value;
  const result=await api("/api/ports");
  $("port-select").replaceChildren();
  const automatic=document.createElement("option");automatic.value="auto";automatic.textContent="自动检测";$("port-select").append(automatic);
  for (const port of result.ports) { const opt=document.createElement("option");opt.value=port.port;opt.textContent=`${port.port} · ${port.can_bridge_uart?"达妙 UART":port.usb?"USB":"串口"}`;opt.disabled=!!port.can_bridge_uart;$("port-select").append(opt); }
  if ([...$("port-select").options].some(o=>o.value===(state.device?.port || old))) $("port-select").value=state.device?.port || old;
}

function render(s) {
  if (s.server_time && state.server_time && s.server_time < state.server_time) return;
  if (seenPort !== (s.device?.port || null) || s.sequence < lastSequence) { history=[];lastSequence=-1;lastPositionTime=null;seenPort=s.device?.port || null; }
  state=s;
  lastFullRender=performance.now();
  motionPanel.render(s);
  if (seenMode!==s.mode) { $("transport-select").value=["usb","canfd","demo"].includes(s.mode)?s.mode:"usb";seenMode=s.mode; }
  const isCan=s.mode==="canfd";
  if (s.connected && isCan) { $("can-channel").value=String(s.device.can_channel);$("can-id").value="0x"+s.device.can_id.toString(16).padStart(2,"0"); }
  $("transport-select").disabled=s.connected || busy;$("can-channel").disabled=s.connected || busy;$("can-id").disabled=s.connected || busy;
  connectionFields();
  $("transport-caption").textContent=s.mode==='demo'?'演示模式 · 合成数据':isCan?"CAN FD · 1M / 5M":"USB 直连 · 普通触觉";
  $("transport-breadcrumb").textContent=s.mode==='demo'?'演示工作台':isCan?"CAN FD 工作台":"USB 工作台";
  const fresh=s.connected && age(s.positions_at)<3;
  document.body.classList.toggle("disconnected", !s.connected);
  $("status-dot").className="status-dot"+(fresh?" online":s.connected?" error":"");
  $("status-text").textContent=s.connected?(s.mode==="demo"?"演示模式":fresh?(isCan?"CAN FD 已连接":"USB 已连接"):"等待新数据"):"未连接";
  $("demo-banner").hidden=s.mode!=="demo";
  $("connect-button").textContent=s.connected?"断开连接":"连接设备";
  $("connect-button").classList.toggle("connected",s.connected);
  $("connect-button").disabled=busy;$("port-select").disabled=s.connected || busy;
  $("record-button").disabled=!s.connected || busy;$("record-button").textContent=s.recording?"停止记录":"开始记录";$("record-button").classList.toggle("recording",s.recording);
  $("record-status").textContent=s.recording?`正在记录 · ${s.record_path}`:s.record_path?`记录已保存 · ${s.record_path}`:"将采样保存为 JSONL，供后续分析。";
  const device=s.device || {}, diag=s.diagnostics || {};
  $('voltage-title').textContent=isCan?'通信链路':'设备供电';
  valueLabel("voltage", isCan?(device.can_channel?'CAN'+device.can_channel:null):device.voltage_mv ? (device.voltage_mv/1000).toFixed(1) : null, isCan?'':'V');
  $("voltage-note").textContent=isCan?'1M 仲裁 / 5M 数据 · FD+BRS':device.voltage_mv?"设备上报的供电电压":"等待设备信息";
  const temps=diag.temperature_c;
  valueLabel("temperature",temps?.length?Math.max(...temps):null,"℃");
  $("temperature-note").textContent=temps?.length?`最低 ${Math.min(...temps)}℃ · 共 ${temps.length} 路`:"10 路电机温度";
  const selection=tactilePanel.render(s);
  const available=Object.values(selection.data).filter(entry=>entry.values?.length).length;
  $('export-snapshot').disabled=!s.connected;
  $('ordinary-summary').textContent=`${Object.values(s.tactile||{}).reduce((n,r)=>n+(r.values?.length||0),0)} 点`;
  $('ordinary-summary-row').hidden=isCan||s.tactile_policy?.ordinary==='disabled';
  $('raw-summary').textContent=selection.rawLabel;
  const raw=s.raw_tactile||{};
  $('raw-integrity').textContent=raw.status==='ready'?(Number.isInteger(raw.returned_frames)?`${raw.returned_frames} / 5 分包 · 7 / 7 区域`:'7 / 7 区域已完整返回'):'等待完整原始帧';
  valueLabel('point-count',selection.total||null,'点');
  $('region-count').textContent=available?(isCan||s.tactile_policy?.ordinary==='disabled'?`七区域 · 原始 ${selection.rawTotal} 点`:`原始 ${selection.rawTotal} 点 · 普通 ${selection.ordinaryTotal} 点`):'等待触觉数据';
  valueLabel("sequence",s.valid_position_samples || 0,"帧");$("last-updated").textContent=s.positions_at?`位置更新于 ${new Date(s.positions_at).toLocaleTimeString("zh-CN",{hour12:false})}`:"尚未开始采集";
  $("firmware").textContent=device.firmware_version||"--";$("sdk-version").textContent=device.sdk_version||"--";$("hardware-version").textContent=device.hardware_version||"--";$("connected-port").textContent=device.port||"--";
  const reports=diag.motor_errors;
  const bad=reports?.filter(r=>Object.values(r).some(Boolean)).length || 0;
  const hasHandError=typeof diag.hand_error_code==="number";
  $("error-summary").textContent=hasHandError?`整手错误码 0x${diag.hand_error_code.toString(16).padStart(4,"0")}`:reports?.length?(bad?`${bad} 轴有错误标记`:"10 轴无错误标记"):"等待诊断数据";
  $("error-summary").className="subtle"+(hasHandError?(diag.hand_error_code?" bad":" good"):reports?.length?(bad?" bad":" good"):"");
  $("current-unit").textContent=isCan?"原始值":"mA";
  $("diagnostic-note").textContent=isCan?"电流保留 0x0A 原始读数，量纲待核对；错误码为整手状态，逐轴标志未读取。":"";
  [...$("joint-rows").children].forEach((row,i)=>{
    const value=s.positions?.[i];row.querySelector(".position-value").textContent=value ?? "--";
    row.querySelector(".position-value").title=value>4096 || value<0?"设备原始反馈，超出名义范围的值保留原样":"原始位置编码";
    row.querySelector(".bar-fill").style.width=value===undefined?"0":`${Math.max(0,Math.min(100,value/4096*100))}%`;
    row.querySelector('.angle').textContent=Number.isFinite(s.pose?.active_rad?.[i])?(s.pose.active_rad[i]*180/Math.PI).toFixed(1):'--';
    row.querySelector(".current").textContent=(isCan?diag.current_raw?.[i]:diag.current_ma?.[i]) ?? "--";row.querySelector(".temp").textContent=temps?.[i] ?? "--";
    const errors=reports?.[i]?Object.entries(reports[i]).filter(([,v])=>v).map(([k])=>errorNames[k]||k):null;
    const label=row.querySelector(".status-label");label.textContent=errors?(errors.length?"异常":"正常"):"--";label.className="status-label"+(errors?(errors.length?" bad":" good"):"");label.title=errors?.join("、")||(isCan?"本次 CAN 查询只提供整手错误码，未读取逐轴标志":"");
  });
  $("diag-age").textContent=diag.temperature_c_at?`温度 / 诊断为低频读取 · 最近 ${Math.floor(age(diag.temperature_c_at))}s 前`:"";
  if (s.positions && s.positions_at!==lastPositionTime) {
    history.push([...s.positions]);if(history.length>90)history.shift();lastPositionTime=s.positions_at;
    drawChart();
  }
  lastSequence=s.sequence;
  $("footer-status").textContent=s.connected?`${device.port} · 整手三维 / 位置 / ${isCan?'原始触觉':'触觉'} · ${s.control?.armed?'位置控制已开启':'控制锁定'}`:"等待连接 · 设备始终由一个采集进程访问";
  window.dispatchEvent(new CustomEvent('omnihand-state',{detail:{state:s,tactile:selection.data,mode:selection.mode,scale:$('color-scale').value,maximum:selection.maximum}}));
  if (!busy) notice(actionError || (s.errors?.length?s.errors.join("；"):null));
}

function drawChart() {
  const canvas=$("position-chart"), rect=canvas.getBoundingClientRect(), dpr=window.devicePixelRatio||1;
  if(!rect.width)return;
  canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);
  const ctx=canvas.getContext("2d");ctx.scale(dpr,dpr);const w=rect.width,h=rect.height,left=49,right=21,top=12,bottom=22;
  ctx.font='9px "Segoe UI", sans-serif';ctx.lineWidth=1;const index=Number($("chart-joint").value || 0), values=history.map(p=>p[index]);
  const ceiling=Math.max(4096,...values);
  for(let i=0;i<=4;i++){const y=top+(h-top-bottom)*i/4;ctx.strokeStyle="#edf1f7";ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(w-right,y);ctx.stroke();ctx.fillStyle="#a4b0c2";ctx.fillText(String(Math.round(ceiling*(1-i/4))),10,y+3);}
  if(!values.length){ctx.fillStyle="#a8b5c8";ctx.textAlign="center";ctx.fillText("等待采样数据",w/2,h/2);return;}
  const points=values.map((v,i)=>[left+(w-left-right)*i/Math.max(1,values.length-1),top+(h-top-bottom)*(1-v/ceiling)]);
  ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.lineTo(points.at(-1)[0],h-bottom);ctx.lineTo(left,h-bottom);ctx.closePath();const gradient=ctx.createLinearGradient(0,top,0,h-bottom);gradient.addColorStop(0,"#4c80ec22");gradient.addColorStop(1,"#4c80ec02");ctx.fillStyle=gradient;ctx.fill();
  ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.lineWidth=1.8;ctx.strokeStyle="#4a79e4";ctx.stroke();const [x,y]=points.at(-1);ctx.fillStyle="#4a79e4";ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fill();
}

async function action(operation) { busy=true;actionError=null;$("connect-button").disabled=true;$("record-button").disabled=true;try{notice(null);await operation();render(await api("/api/state"));}catch(e){actionError=e.message;notice(e.message);}finally{busy=false;$("connect-button").disabled=false;$("record-button").disabled=!state.connected;$("port-select").disabled=state.connected;} }
function connectionFields() { const can=$("transport-select").value==="canfd";$("port-select").hidden=can || $("transport-select").value==="demo";$("refresh-ports").hidden=$("port-select").hidden;$("can-channel").hidden=!can;$("can-id").hidden=!can; }
$("transport-select").addEventListener("change",connectionFields);
$("connect-button").addEventListener("click",()=>action(async()=>{await api(state.connected?"/api/disconnect":"/api/connect",state.connected?{}:{mode:$("transport-select").value,port:$("port-select").value,hand:state.device?.hand || "right",device_id:state.mode==="usb"?state.device?.device_id || 1:1,can_channel:Number($("can-channel").value),can_id:Number($("can-id").value)});}));
$("record-button").addEventListener("click",()=>action(()=>api("/api/recording",{enabled:!state.recording})));
$("refresh-ports").addEventListener("click",()=>refreshPorts().catch(e=>notice(e.message)));
$("chart-joint").addEventListener("change",drawChart);window.addEventListener("resize",drawChart);
$('export-snapshot').addEventListener('click',()=>{
  const snapshot={format:'omnihand-lab-snapshot-v1',exported_at:new Date().toISOString(),display_tactile_source:'raw_and_ordinary',model_tactile_source:tactilePanel.mode,...state};
  const url=URL.createObjectURL(new Blob([JSON.stringify(snapshot,null,2)],{type:'application/json'}));
  const anchor=document.createElement('a');anchor.href=url;anchor.download=`omnihand_snapshot_${new Date().toISOString().replace(/[:.]/g,'-')}.json`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
window.addEventListener('omnihand-region',event=>{if(event.detail!=='ALL')tactilePanel.selectRegion(event.detail);});
initializeRows();drawChart();refreshPorts().catch(e=>notice(e.message));
(async function poll(){
  let delay=500;
  try{
    const sample=await api("/api/state");
    const fast=sample.connected&&sample.control?.armed;
    if(fast)delay=65;
    if(sample.server_time<state.server_time)return;
    const c=sample.control, previous=state.control;
    const sameControl=previous&&['session_id','status','armed','running','power_state','motion_id','error'].every(key=>c?.[key]===previous[key])
      && c?.stage?.index===previous.stage?.index;
    if(fast&&sameControl&&(age(sample.positions_at)<3)===(age(state.positions_at)<3)&&performance.now()-lastFullRender<220){
      state=sample;
      motionPanel.renderFeedback(sample);
      window.dispatchEvent(new CustomEvent('omnihand-pose',{detail:sample}));
    }else{
      render(sample);
    }
  }catch(e){
    $("status-dot").className="status-dot error";
    $("status-text").textContent="服务未响应";
    notice("无法读取本地服务，请确认采集程序仍在运行。 "+e.message);
  }finally{
    setTimeout(poll,delay);
  }
})();
