const $=id=>document.getElementById(id);
const names=['拇指旋转','拇指侧摆','拇指屈伸','食指侧摆','食指屈伸','中指屈伸','无名指侧摆','无名指屈伸','小指侧摆','小指屈伸'];
const valid=values=>Array.isArray(values)&&values.length===10&&values.every(v=>Number.isInteger(v)&&v>=0&&v<=4096);
const JOG_INTERVAL_MS=45;

export class MotionPanel{
  constructor(api,refresh){
    this.api=api;this.refresh=refresh;this.state={};this.session=null;this.presets=[];this.target=null;this.preset=null;this.name='尚未选择目标';this.pending=0;this.error=null;
    this.jogTargets=new Map();this.jogBusy=false;this.jogTimer=null;this.jogGeneration=0;this.lastJog=0;
    names.forEach((name,i)=>{
      const row=document.createElement('label');row.className='axis-control';
      const title=document.createElement('span');title.textContent=`${String(i+1).padStart(2,'0')} ${name}`;
      const range=document.createElement('input');range.type='range';range.min=0;range.max=4096;range.step=1;range.value=0;range.setAttribute('aria-label',`${name}目标滑块`);
      const number=document.createElement('input');number.type='number';number.min=0;number.max=4096;number.step=1;number.value=0;number.setAttribute('aria-label',`${name}目标值`);
      const feedback=document.createElement('output');feedback.className='axis-feedback';feedback.textContent='反馈 --';
      const change=(source,other,live)=>{
        other.value=source.value;if(!this.target)return;
        this.target[i]=source.value===''?NaN:Number(source.value);
        if(this.preset){this.preset=null;this.markSelection();}
        this.name='自定义目标';this.error=null;
        if(live&&valid(this.target))this.queueJoint(i+1,this.target[i]);this.render(this.state);
      };
      range.addEventListener('input',()=>change(range,number,true));range.addEventListener('change',()=>this.flushJog());
      number.addEventListener('input',()=>change(number,range,false));number.addEventListener('change',()=>change(number,range,true));
      number.addEventListener('keydown',event=>{if(event.key==='Enter'){change(number,range,true);this.flushJog();}});
      row.append(title,range,number,feedback);$('axis-controls').append(row);
    });
    $('control-arm').addEventListener('click',()=>this.send(this.state.control?.armed?'lock':'arm'));
    $('motor-enable').addEventListener('click',()=>this.send('enable'));
    $('motor-disable').addEventListener('click',()=>this.send('disable'));
    $('motion-stop').addEventListener('click',()=>this.send('stop'));
    $('motion-execute').addEventListener('click',()=>this.send('move'));
    $('motion-preset-select').addEventListener('change',event=>{
      const preset=this.presets.find(item=>item.id===event.target.value);
      if(preset)this.selectPreset(preset);
    });
    $('motion-capture').addEventListener('click',()=>{
      if(!Array.isArray(this.state.positions))return;
      const current=this.state.positions;this.setTarget(current.map(v=>Math.max(0,Math.min(4096,v))),null,'当前姿态');
      this.showNote('已同步当前反馈为目标。');
    });
    $('motion-save').addEventListener('click',()=>this.save());
    $('motion-duration').addEventListener('input',()=>this.render(this.state));
    $('motion-mode').addEventListener('change',()=>this.render(this.state));
    $('control-mode').addEventListener('change',()=>{this.cancelJog();this.render(this.state);});
    $('mixed-current').addEventListener('input',()=>this.cancelJog());$('mixed-velocity').addEventListener('input',()=>this.cancelJog());
  }
  async catalog(){
    const session=this.session;
    const data=await this.api('/api/control');
    if(session!==this.session)return;
    this.presets=data.presets;
    const select=$('motion-preset-select');
    const placeholder=document.createElement('option');
    placeholder.value='';placeholder.textContent='选择预设';placeholder.disabled=true;
    select.replaceChildren(placeholder);
    $('motion-presets').replaceChildren();
    for(const preset of this.presets){
      const option=document.createElement('option');
      option.value=preset.id;option.textContent=preset.name;select.append(option);
      const button=document.createElement('button');button.className='preset-card';button.dataset.preset=preset.id;button.setAttribute('aria-pressed','false');button.title=preset.description;
      const icon=document.createElement('span');icon.className='preset-icon';icon.textContent=preset.icon;
      const title=document.createElement('strong');title.textContent=preset.name;
      icon.setAttribute('aria-hidden','true');button.append(icon,title);
      button.addEventListener('click',()=>this.selectPreset(preset));
      $('motion-presets').append(button);
    }
    this.markSelection();this.render(this.state);
  }
  selectPreset(preset){
    this.cancelJog();
    this.showNote('');
    this.setTarget(preset.positions,preset.id,preset.name);
  }
  showNote(message){
    const note=$('motion-preview-note');
    note.textContent=message;
    note.hidden=!message;
  }
  setTarget(values,id,name){
    this.target=[...values];this.preset=id;this.name=name;this.error=null;
    [...$('axis-controls').children].forEach((row,i)=>row.querySelectorAll('input').forEach(input=>input.value=values[i]));this.markSelection();this.render(this.state);
  }
  markSelection(){
    $('motion-preset-select').value=this.preset||'';
    document.querySelectorAll('.preset-card').forEach(button=>{
      const selected=button.dataset.preset===this.preset;
      button.classList.toggle('selected',selected);
      button.setAttribute('aria-pressed',String(selected));
    });
  }
  profile(){
    const mode=$('control-mode').value||'position',profile={control_mode:mode};
    if(mode!=='position'){
      const current=Number($('mixed-current').value);if($('mixed-current').value===''||!Number.isInteger(current)||current<0||current>1000)throw new Error('电流必须为0–1000mA整数');
      profile.currents_ma=Array(10).fill(current);
    }
    if(mode==='pvt'){
      const velocity=Number($('mixed-velocity').value);if($('mixed-velocity').value===''||!Number.isInteger(velocity)||velocity< -4095||velocity>4096)throw new Error('速度原始值必须为-4095–4096整数');
      profile.velocities=Array(10).fill(velocity);
    }
    return profile;
  }
  canJog(){
    const c=this.state.control||{},mode=$('control-mode').value||'position';
    return this.state.connected&&c.armed&&c.power_state!==0&&!c.running&&!this.pending&&Date.now()-Date.parse(this.state.positions_at)<3000&&c.capabilities?.includes('joint')&&(mode==='position'||c.capabilities.includes('mixed_joint'));
  }
  cancelJog(){clearTimeout(this.jogTimer);this.jogTimer=null;this.jogTargets.clear();this.jogGeneration++;}
  queueJoint(index,position){
    if(!this.canJog())return;
    this.jogTargets.set(index,position);
    this.scheduleJog();
  }
  scheduleJog(){
    if(this.jogBusy||this.jogTimer!==null||!this.jogTargets.size)return;
    this.jogTimer=setTimeout(()=>this.flushJog(),Math.max(0,this.lastJog+JOG_INTERVAL_MS-Date.now()));
  }
  async flushJog(){
    clearTimeout(this.jogTimer);this.jogTimer=null;
    if(this.jogBusy||!this.jogTargets.size)return;
    if(!this.canJog()){this.cancelJog();return;}
    const generation=this.jogGeneration,session=this.session,[index,position]=this.jogTargets.entries().next().value;
    this.jogTargets.delete(index);this.jogBusy=true;this.lastJog=Date.now();
    try{
      await this.api('/api/control',{action:'joint',session_id:session,index,position,...this.profile()});
    }catch(error){if(generation===this.jogGeneration){this.cancelJog();this.fail(error);}}
    finally{
      this.jogBusy=false;
      this.scheduleJog();
    }
  }
  fail(error){this.error=error.message;this.render(this.state);}
  async send(action){
    this.cancelJog();
    this.pending++;this.error=null;this.render(this.state);
    const body={action,session_id:this.state.control?.session_id};
    if(action==='move'){
      if(!valid(this.target)){this.pending--;this.fail(new Error('请先选择有效目标'));return;}
      try{Object.assign(body,this.preset?{preset_id:this.preset}:{positions:[...this.target]},{execution_mode:$('motion-mode').value},this.profile());}
      catch(error){this.pending--;this.fail(error);return;}
      if(body.execution_mode==='smooth')body.duration=Number($('motion-duration').value);
      this.showNote('');
    }
    try{await this.api('/api/control',body);await this.refresh();}catch(error){this.fail(error);}finally{this.pending--;this.render(this.state);}
  }
  async save(){
    try{
      if(!valid(this.target))throw new Error('请先选择有效目标');
      const result=await this.api('/api/control',{action:'save',name:$('motion-name').value,positions:[...this.target]});
      this.preset=result.preset.id;this.name=result.preset.name;this.error=null;await this.catalog();$('motion-name').value='';this.showNote('自定义动作已保存在本机。');
    }catch(error){this.fail(error);}
  }
  renderFeedback(state){
    this.state=state;
    const fresh=state.connected&&Date.now()-Date.parse(state.positions_at)<3000;
    [...$('axis-controls').children].forEach((row,i)=>{
      const output=row.querySelector('output');
      const value=`${fresh?'反馈':'上次'} ${state.positions?.[i]??'--'}`;
      if(output.textContent!==value)output.textContent=value;
      output.classList.toggle('stale',!fresh);
    });
  }
  render(state){
    this.state=state;const c=state.control||{},available=!!state.connected&&!!c.available,fresh=available&&Date.now()-Date.parse(state.positions_at)<3000,pending=this.pending>0;
    if(c.session_id&&c.session_id!==this.session){this.cancelJog();this.session=c.session_id;this.target=null;this.preset=null;this.name='尚未选择目标';this.markSelection();this.catalog().catch(e=>this.fail(e));}
    if(!c.armed||!fresh||c.running)this.cancelJog();
    if(!this.target&&fresh&&Array.isArray(state.positions)){
      this.target=state.positions.map(v=>Math.max(0,Math.min(4096,v)));this.name='当前反馈目标';
      [...$('axis-controls').children].forEach((row,i)=>row.querySelectorAll('input').forEach(input=>input.value=this.target[i]));
    }
    this.renderFeedback(state);
    $('control-badge').textContent=c.running?'正在执行':c.armed?'控制已开启':'控制已锁定';$('control-badge').classList.toggle('armed',!!c.armed);
    $('control-scope').textContent=c.armed?'位置控制已开启':'只读采集 · 控制锁定';
    $('control-scope-note').textContent=c.armed?'滑块直接控制单轴；预设需点击发送。':'三维显示反馈；开启控制后可拖动关节。';
    $('session-access').textContent=c.armed?'CONTROL ENABLED':'CONTROL LOCKED';$('session-access').classList.toggle('armed',!!c.armed);
    $('control-arm').textContent=c.armed?'锁定控制':'开启控制';$('control-arm').disabled=!available||(pending&&!c.running);
    const power=c.capabilities?.includes('power');$('motor-enable').hidden=!power;$('motor-disable').hidden=!power;
    $('motor-enable').disabled=!available||!c.armed||c.running||pending||c.power_state===1;
    $('motor-disable').disabled=!available||(pending&&!c.running)||c.power_state===0;
    $('motion-stop').disabled=!available||(!c.armed&&!c.running&&!c.write_attempted);
    $('motion-power').textContent=power?c.power_state===1?'电机已使能':c.power_state===0?'电机未使能':'电机状态待查询':'USB SDK不提供使能接口，需预先使能';
    $('motion-target').textContent=this.name;
    $('motion-target-values').textContent=valid(this.target)?this.target.join(' · '):'选择预设后点击发送。';
    const mode=$('control-mode').value||'position';
    for(const option of $('control-mode').options||[])option.disabled=option.value!=='position'&&!c.capabilities?.includes(option.value);
    $('mixed-current-field').hidden=mode==='position';$('mixed-velocity-field').hidden=mode!=='pvt';
    const sendMode=$('motion-mode');
    const stagedOption=Array.from(sendMode.options||[]).find(option=>option.value==='staged');
    if(stagedOption)stagedOption.disabled=c.hand==='left';
    if(mode!=='position'||(c.hand==='left'&&sendMode.value==='staged'))sendMode.value='direct';
    $('control-mode').disabled=!!c.running||pending;$('mixed-current').disabled=!!c.running||pending;$('mixed-velocity').disabled=!!c.running||pending;
    const smooth=sendMode.value==='smooth',staged=sendMode.value==='staged';
    const duration=Number($('motion-duration').value),durationOK=!smooth||(Number.isFinite(duration)&&duration>=.5&&duration<=30);
    $('motion-duration-field').hidden=!smooth;$('motion-mode').disabled=mode!=='position'||!!c.running||pending;$('motion-duration').disabled=!!c.running||pending;
    $('motion-execute').textContent=staged?'分段发送':smooth?'执行渐变':'直接发送';
    $('motion-execute').disabled=!fresh||!c.armed||c.power_state===0||c.running||pending||!valid(this.target)||!durationOK;
    $('motion-preset-select').disabled=!available||!!c.running||pending||!this.presets.length;
    $('motion-save').disabled=!valid(this.target)||c.running;$('motion-capture').disabled=!fresh||c.running;
    const canJog=this.canJog();
    let jointHint='开启控制并使能后可拖动。';
    if(canJog)jointHint='拖动发送单轴目标，绿色数字为位置反馈。';
    else if(c.running)jointHint='完成或停止当前动作后可拖动。';
    else if(pending)jointHint='正在处理控制请求…';
    else if(mode!=='position'&&!c.capabilities?.includes('mixed_joint'))jointHint='当前连接的单轴仅支持位置模式。';
    $('joint-live-status').textContent=jointHint;
    document.querySelectorAll('.preset-card').forEach(b=>b.disabled=!!c.running);document.querySelectorAll('#axis-controls input').forEach(input=>input.disabled=!canJog||!this.target);
    $('motion-progress').value=c.progress||0;$('motion-progress').hidden=!c.motion_id||(c.running&&c.execution_mode==='direct')||(!c.running&&c.status!=='completed');
    $('motion-message').textContent=this.error||(pending&&!c.running?'正在提交指令…':c.error||c.message)||'请先连接设备';$('motion-message').classList.toggle('bad',!!this.error||(!pending&&!!c.error));
    let motionDetail=mode==='pvt'?'位置＋速度＋电流':mode==='pt'?'位置＋电流':staged?'空手切换，逐段回读到位':'';
    if(smooth)motionDetail='电脑插值，每步最多 80 个位置单位';
    if(c.running && c.stage){
      motionDetail=`${c.name} · ${c.stage.index}/${c.stage.total} ${c.stage.name}`;
    }else if(c.running && c.execution_mode==='smooth'){
      motionDetail=`${c.name} · ${Math.round((c.progress||0)*100)}% · 渐变至少 ${c.effective_duration}s`;
    }else if(c.running && c.execution_mode==='direct'){
      motionDetail=`${c.name} · 等待到位反馈`;
    }else if(c.status==='completed'){
      motionDetail=c.name;
    }
    if(Number.isInteger(c.position_error)&&(c.running||c.status==='completed')){
      motionDetail+=` · 最大偏差 ${c.position_error}`;
      if(c.arrival_tolerance>0)motionDetail+=` / ${c.arrival_tolerance}`;
    }
    $('motion-detail').textContent=motionDetail;
  }
}
