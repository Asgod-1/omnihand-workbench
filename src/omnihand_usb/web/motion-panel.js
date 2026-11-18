const $=id=>document.getElementById(id);
const names=['拇指旋转','拇指侧摆','拇指屈伸','食指侧摆','食指屈伸','中指屈伸','无名指侧摆','无名指屈伸','小指侧摆','小指屈伸'];
const valid=values=>Array.isArray(values)&&values.length===10&&values.every(v=>Number.isInteger(v)&&v>=0&&v<=4096);

export class MotionPanel{
  constructor(api,refresh){
    this.api=api;this.refresh=refresh;this.state={};this.session=null;this.presets=[];this.target=null;this.preset=null;this.name='尚未选择目标';this.pending=0;this.error=null;this.previewSerial=0;
    names.forEach((name,i)=>{
      const row=document.createElement('label');row.className='axis-control';
      const title=document.createElement('span');title.textContent=`${String(i+1).padStart(2,'0')} ${name}`;
      const range=document.createElement('input');range.type='range';range.min=0;range.max=4096;range.step=1;range.value=0;range.setAttribute('aria-label',`${name}目标滑块`);
      const number=document.createElement('input');number.type='number';number.min=0;number.max=4096;number.step=1;number.value=0;number.setAttribute('aria-label',`${name}目标值`);
      const change=(source,other)=>{
        other.value=source.value;if(!this.target)this.target=Array(10).fill(0);
        this.target[i]=source.value===''?NaN:Number(source.value);this.preset=null;this.name='自定义目标';this.error=null;this.markSelection();
        clearTimeout(this.previewTimer);this.clearPreview();this.previewTimer=setTimeout(()=>{if(valid(this.target))this.preview().catch(e=>this.fail(e));},250);this.render(this.state);
      };
      range.addEventListener('input',()=>change(range,number));number.addEventListener('input',()=>change(number,range));row.append(title,range,number);$('axis-controls').append(row);
    });
    $('control-arm').addEventListener('click',()=>this.send(this.state.control?.armed?'lock':'arm'));
    $('motor-enable').addEventListener('click',()=>this.send('enable'));
    $('motor-disable').addEventListener('click',()=>this.send('disable'));
    $('motion-stop').addEventListener('click',()=>this.send('stop'));
    $('motion-execute').addEventListener('click',()=>this.send('move'));
    $('motion-preview').addEventListener('click',()=>this.preview().catch(e=>this.fail(e)));
    $('motion-measured').addEventListener('click',()=>{this.clearPreview();$('motion-preview-note').textContent='三维已恢复实测姿态，当前目标保留。';});
    $('motion-capture').addEventListener('click',()=>{
      if(!Array.isArray(this.state.positions))return;
      const current=this.state.positions;this.setTarget(current.map(v=>Math.max(0,Math.min(4096,v))),null,'当前姿态');
      $('motion-preview-note').textContent=current.some(v=>v<0||v>4096)?'已取当前反馈；目标限于0–4096，原始反馈保留。':'已取当前反馈为目标，尚未下发。';this.preview().catch(e=>this.fail(e));
    });
    $('motion-save').addEventListener('click',()=>this.save());
    $('motion-duration').addEventListener('input',()=>this.render(this.state));
  }
  async catalog(){
    const data=await this.api('/api/control');this.presets=data.presets;$('motion-presets').replaceChildren();
    for(const preset of this.presets){
      const button=document.createElement('button');button.className='preset-card';button.dataset.preset=preset.id;button.setAttribute('aria-pressed','false');button.title=`${preset.description}；点击只预览`;
      const icon=document.createElement('span');icon.className='preset-icon';icon.textContent=preset.icon;
      const title=document.createElement('strong');title.textContent=preset.name;
      const source=document.createElement('small');source.textContent=preset.source==='user'?'本机保存':preset.source.startsWith('official')?'官方手势':'位置插值';button.append(icon,title,source);
      button.addEventListener('click',()=>{this.setTarget(preset.positions,preset.id,preset.name);this.preview().catch(e=>this.fail(e));});$('motion-presets').append(button);
    }
    this.markSelection();this.render(this.state);
  }
  setTarget(values,id,name){
    this.previewSerial++;this.target=[...values];this.preset=id;this.name=name;this.error=null;
    [...$('axis-controls').children].forEach((row,i)=>row.querySelectorAll('input').forEach(input=>input.value=values[i]));this.markSelection();this.render(this.state);
  }
  markSelection(){document.querySelectorAll('.preset-card').forEach(b=>{const yes=b.dataset.preset===this.preset;b.classList.toggle('selected',yes);b.setAttribute('aria-pressed',String(yes));});}
  clearPreview(){this.previewSerial++;window.dispatchEvent(new CustomEvent('omnihand-action-preview',{detail:null}));}
  async preview(){
    if(!valid(this.target))throw new Error('十轴目标必须为0–4096的整数');
    const serial=++this.previewSerial,session=this.session;
    const data=await this.api('/api/control',{action:'preview',positions:[...this.target]});
    if(serial!==this.previewSerial||session!==this.session)return;
    if(!data.pose?.joints_rad)throw new Error(data.pose_error||'三维姿态未能解算，目标尚未下发');
    window.dispatchEvent(new CustomEvent('omnihand-action-preview',{detail:{...data,name:this.name}}));
    $('motion-preview-note').textContent=`三维预览：${this.name} · 尚未向设备下发。`;this.error=null;this.render(this.state);
  }
  fail(error){this.error=error.message;this.render(this.state);}
  async send(action){
    this.pending++;this.error=null;this.render(this.state);
    const body={action,session_id:this.state.control?.session_id};
    if(action==='move'){
      if(!valid(this.target)){this.pending--;this.fail(new Error('请先选择有效目标'));return;}
      Object.assign(body,this.preset?{preset_id:this.preset}:{positions:[...this.target]},{duration:Number($('motion-duration').value)});
      this.clearPreview();$('motion-preview-note').textContent='三维显示实际反馈；动作进度以设备回读为准。';
    }
    try{await this.api('/api/control',body);await this.refresh();}catch(error){this.fail(error);}finally{this.pending--;this.render(this.state);}
  }
  async save(){
    try{
      if(!valid(this.target))throw new Error('请先选择有效目标');
      const result=await this.api('/api/control',{action:'save',name:$('motion-name').value,positions:[...this.target]});
      this.preset=result.preset.id;this.name=result.preset.name;this.error=null;await this.catalog();$('motion-name').value='';$('motion-preview-note').textContent='自定义动作已保存在本机，尚未下发。';
    }catch(error){this.fail(error);}
  }
  render(state){
    this.state=state;const c=state.control||{},available=!!state.connected&&!!c.available,fresh=available&&Date.now()-Date.parse(state.positions_at)<3000,pending=this.pending>0;
    if(c.session_id&&c.session_id!==this.session){this.session=c.session_id;this.target=null;this.preset=null;this.name='尚未选择目标';this.clearPreview();this.catalog().catch(e=>this.fail(e));}
    $('control-badge').textContent=c.running?'正在执行':c.armed?'控制已开启':'控制已锁定';$('control-badge').classList.toggle('armed',!!c.armed);
    $('control-scope').textContent=c.armed?'位置控制已开启':'只读采集 · 控制锁定';
    $('control-scope-note').textContent=c.armed?'选择目标后点击执行。':'可预览动作；开启控制后才能执行。';
    $('session-access').textContent=c.armed?'CONTROL ENABLED':'CONTROL LOCKED';$('session-access').classList.toggle('armed',!!c.armed);
    $('control-arm').textContent=c.armed?'锁定控制':'开启控制';$('control-arm').disabled=!available||(pending&&!c.running);
    const power=c.capabilities?.includes('power');$('motor-enable').hidden=!power;$('motor-disable').hidden=!power;
    $('motor-enable').disabled=!available||!c.armed||c.running||pending||c.power_state===1;
    $('motor-disable').disabled=!available||(pending&&!c.running)||c.power_state===0;
    $('motion-stop').disabled=!available||(!c.armed&&!c.running&&!c.write_attempted);
    $('motion-power').textContent=power?c.power_state===1?'电机已使能':c.power_state===0?'电机未使能':'电机状态待查询':'USB SDK不提供使能接口，需预先使能';
    $('motion-target').textContent=this.name;
    $('motion-target-values').textContent=valid(this.target)?this.target.join(' · '):'点击一个手势，只预览，不下发。';
    const duration=Number($('motion-duration').value),durationOK=Number.isFinite(duration)&&duration>=.5&&duration<=30;
    $('motion-execute').disabled=!fresh||!c.armed||c.power_state===0||c.running||pending||!valid(this.target)||!durationOK;
    $('motion-preview').disabled=!valid(this.target)||c.running;$('motion-save').disabled=!valid(this.target)||c.running;$('motion-capture').disabled=!fresh||c.running;
    document.querySelectorAll('.preset-card').forEach(b=>b.disabled=!!c.running);document.querySelectorAll('#axis-controls input').forEach(input=>input.disabled=!!c.running||!this.target);
    $('motion-progress').value=c.progress||0;$('motion-progress').hidden=!c.motion_id||(!c.running&&c.status!=='completed');
    $('motion-message').textContent=this.error||c.error||c.message||'请先连接设备';$('motion-message').classList.toggle('bad',!!this.error||!!c.error);
    $('motion-detail').textContent=c.motion_id?`${c.running?'执行中':c.status==='completed'?'已完成':'上次动作'}：${c.name} · ${Math.round((c.progress||0)*100)}% · 渐变至少 ${c.effective_duration}s`:'位置目标0–4096 · 单次步长≤80 · 最快10次/秒 · 不代表力或力矩';
    if(c.running)$('motion-preview-note').textContent='动作正在执行，三维显示实际反馈。';
  }
}
