const $=id=>document.getElementById(id);
const names=['拇指旋转','拇指侧摆','拇指屈伸','食指侧摆','食指屈伸','中指屈伸','无名指侧摆','无名指屈伸','小指侧摆','小指屈伸'];
const valid=values=>Array.isArray(values)&&values.length===10&&values.every(v=>Number.isInteger(v)&&v>=0&&v<=4096);

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
        this.target[i]=source.value===''?NaN:Number(source.value);this.preset=null;this.name='自定义目标';this.error=null;this.markSelection();
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
    $('motion-capture').addEventListener('click',()=>{
      if(!Array.isArray(this.state.positions))return;
      const current=this.state.positions;this.setTarget(current.map(v=>Math.max(0,Math.min(4096,v))),null,'当前姿态');
      $('motion-preview-note').textContent='已取当前反馈为目标，三维持续显示反馈。';
    });
    $('motion-save').addEventListener('click',()=>this.save());
    $('motion-duration').addEventListener('input',()=>this.render(this.state));
    $('motion-mode').addEventListener('change',()=>this.render(this.state));
    $('control-mode').addEventListener('change',()=>{this.cancelJog();this.render(this.state);});
    $('mixed-current').addEventListener('input',()=>this.cancelJog());$('mixed-velocity').addEventListener('input',()=>this.cancelJog());
  }
  async catalog(){
    const data=await this.api('/api/control');this.presets=data.presets;$('motion-presets').replaceChildren();
    for(const preset of this.presets){
      const button=document.createElement('button');button.className='preset-card';button.dataset.preset=preset.id;button.setAttribute('aria-pressed','false');button.title=`${preset.description}；选择后点击发送，三维保持实时反馈`;
      const icon=document.createElement('span');icon.className='preset-icon';icon.textContent=preset.icon;
      const title=document.createElement('strong');title.textContent=preset.name;
      const source=document.createElement('small');source.textContent=preset.source==='user'?'本机保存':preset.source.startsWith('official')?'官方手势':'位置插值';button.append(icon,title,source);
      button.addEventListener('click',()=>{this.cancelJog();this.setTarget(preset.positions,preset.id,preset.name);$('motion-preview-note').textContent='目标已选择，点击发送才执行；三维持续显示反馈。';});$('motion-presets').append(button);
    }
    this.markSelection();this.render(this.state);
  }
  setTarget(values,id,name){
    this.target=[...values];this.preset=id;this.name=name;this.error=null;
    [...$('axis-controls').children].forEach((row,i)=>row.querySelectorAll('input').forEach(input=>input.value=values[i]));this.markSelection();this.render(this.state);
  }
  markSelection(){document.querySelectorAll('.preset-card').forEach(b=>{const yes=b.dataset.preset===this.preset;b.classList.toggle('selected',yes);b.setAttribute('aria-pressed',String(yes));});}
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
    this.jogTargets.set(index,position);clearTimeout(this.jogTimer);
    this.jogTimer=setTimeout(()=>this.flushJog(),Math.max(0,this.lastJog+120-Date.now()));
  }
  async flushJog(){
    clearTimeout(this.jogTimer);this.jogTimer=null;
    if(this.jogBusy||!this.jogTargets.size)return;
    if(!this.canJog()){this.cancelJog();return;}
    const generation=this.jogGeneration,session=this.session,[index,position]=this.jogTargets.entries().next().value;
    this.jogTargets.delete(index);this.jogBusy=true;this.lastJog=Date.now();
    try{
      await this.api('/api/control',{action:'joint',session_id:session,index,position,...this.profile()});
      if(generation===this.jogGeneration&&session===this.session)await this.refresh();
    }catch(error){if(generation===this.jogGeneration){this.cancelJog();this.fail(error);}}
    finally{
      this.jogBusy=false;
      if(this.jogTargets.size)this.jogTimer=setTimeout(()=>this.flushJog(),Math.max(0,this.lastJog+120-Date.now()));
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
      $('motion-preview-note').textContent='三维持续显示实际反馈，目标到位以设备回读为准。';
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
    if(c.session_id&&c.session_id!==this.session){this.cancelJog();this.session=c.session_id;this.target=null;this.preset=null;this.name='尚未选择目标';this.catalog().catch(e=>this.fail(e));}
    if(!c.armed||!fresh||c.running)this.cancelJog();
    if(!this.target&&fresh&&Array.isArray(state.positions)){
      this.target=state.positions.map(v=>Math.max(0,Math.min(4096,v)));this.name='当前反馈目标';
      [...$('axis-controls').children].forEach((row,i)=>row.querySelectorAll('input').forEach(input=>input.value=this.target[i]));
    }
    [...$('axis-controls').children].forEach((row,i)=>{const output=row.querySelector('output');output.textContent=`${fresh?'反馈':'上次反馈'} ${state.positions?.[i]??'--'}`;output.classList.toggle('stale',!fresh);});
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
    $('motion-target-values').textContent=valid(this.target)?this.target.join(' · '):'选择预设后点击发送，三维持续显示反馈。';
    const mode=$('control-mode').value||'position';
    for(const option of $('control-mode').options||[])option.disabled=option.value!=='position'&&!c.capabilities?.includes(option.value);
    $('mixed-current-field').hidden=mode==='position';$('mixed-velocity-field').hidden=mode!=='pvt';
    if(mode!=='position')$('motion-mode').value='direct';
    $('control-mode').disabled=!!c.running||pending;$('mixed-current').disabled=!!c.running||pending;$('mixed-velocity').disabled=!!c.running||pending;
    const smooth=$('motion-mode').value==='smooth',duration=Number($('motion-duration').value),durationOK=!smooth||(Number.isFinite(duration)&&duration>=.5&&duration<=30);
    $('motion-duration-field').hidden=!smooth;$('motion-mode').disabled=mode!=='position'||!!c.running||pending;$('motion-duration').disabled=!!c.running||pending;
    $('motion-execute').textContent=smooth?'执行渐变':'直接发送';
    $('motion-execute').disabled=!fresh||!c.armed||c.power_state===0||c.running||pending||!valid(this.target)||!durationOK;
    $('motion-save').disabled=!valid(this.target)||c.running;$('motion-capture').disabled=!fresh||c.running;
    $('joint-live-status').textContent=this.canJog()?'拖动滑块直接发送该关节；数值输入后按回车或移开焦点发送。':mode!=='position'&&!c.capabilities?.includes('mixed_joint')?'当前连接的单轴仅支持位置模式，混合控制可用整手发送。':'开启控制并使能后可拖动；绿色数字始终为实际反馈。';
    document.querySelectorAll('.preset-card').forEach(b=>b.disabled=!!c.running);document.querySelectorAll('#axis-controls input').forEach(input=>input.disabled=!this.canJog()||!this.target);
    $('motion-progress').value=c.progress||0;$('motion-progress').hidden=!c.motion_id||(c.running&&c.execution_mode==='direct')||(!c.running&&c.status!=='completed');
    $('motion-message').textContent=this.error||c.error||c.message||'请先连接设备';$('motion-message').classList.toggle('bad',!!this.error||!!c.error);
    $('motion-detail').textContent=c.motion_id?`${c.running?'执行中':c.status==='completed'?'已完成':'上次动作'}：${c.name} · ${c.execution_mode==='direct'?`一次发送完整目标${c.running?' · 等待到位反馈':''}`:`${Math.round((c.progress||0)*100)}% · 渐变至少 ${c.effective_duration}s`}`:smooth?'位置目标0–4096 · 电脑插值，每步≤80 · 不代表力或力矩':'位置目标0–4096 · 一次发送十轴目标 · 到位速度由设备决定';
    if(c.running)$('motion-preview-note').textContent='动作正在执行，三维显示实际反馈。';
  }
}
