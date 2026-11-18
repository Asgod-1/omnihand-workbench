import test from 'node:test';
import assert from 'node:assert/strict';
import {MotionPanel} from '../src/omnihand_usb/web/motion-panel.js';

// Exercise the panel's controls without opening a browser or device.
class Element{
  constructor(tag='div'){this.tag=tag;this.value='';this.children=[];this.listeners={};this.hidden=false;this.disabled=false;this.classList={toggle(){}};}
  addEventListener(name,handler){this.listeners[name]=handler;}
  setAttribute(){}
  append(...children){this.children.push(...children);}
  querySelectorAll(){return this.children.filter(c=>c.tag==='input');}
}
function fixture(t){
  const elements=new Map(),get=id=>{
    if(!elements.has(id))elements.set(id,new Element());
    return elements.get(id);
  };
  get('motion-mode').value='direct';get('motion-duration').value='4';
  const previous={document:globalThis.document,window:globalThis.window,CustomEvent:globalThis.CustomEvent};
  globalThis.document={getElementById:get,createElement:tag=>new Element(tag),querySelectorAll:selector=>selector==='#axis-controls input'?get('axis-controls').children.flatMap(row=>row.querySelectorAll()):[]};
  globalThis.window={dispatchEvent(){}};globalThis.CustomEvent=class{constructor(type,options){this.type=type;this.detail=options.detail;}};
  t.after(()=>Object.assign(globalThis,previous));
  const calls=[],panel=new MotionPanel(async(path,body)=>{calls.push({path,body});return {};},async()=>{});
  panel.session='demo-session';panel.target=Array(10).fill(1200);
  const state={connected:true,mode:'demo',positions_at:new Date().toISOString(),control:{session_id:panel.session,available:true,armed:true,running:false,power_state:1,capabilities:['position','power']}};
  panel.render(state);
  return {get,panel,state,calls};
}

test('default button sends exactly one complete target without a duration',async t=>{
  const {get,panel,calls}=fixture(t);
  get('motion-duration').value='invalid';panel.render(panel.state);
  assert.equal(get('motion-duration-field').hidden,true);
  assert.equal(get('motion-execute').disabled,false);
  await get('motion-execute').listeners.click();
  assert.equal(calls.length,1);
  assert.deepEqual(calls[0].body,{action:'move',session_id:'demo-session',execution_mode:'direct',positions:Array(10).fill(1200)});
});

test('choosing interpolation only changes the form and does not send a command',t=>{
  const {get,calls}=fixture(t);
  get('motion-mode').value='smooth';get('motion-mode').listeners.change();
  assert.equal(get('motion-duration-field').hidden,false);
  get('motion-duration').value='0';get('motion-duration').listeners.input();
  assert.equal(get('motion-execute').disabled,true);
  get('motion-mode').value='direct';get('motion-mode').listeners.change();
  assert.equal(get('motion-execute').disabled,false);
  assert.equal(calls.length,0);
});

test('optional interpolation sends the selected mode and duration',async t=>{
  const {get,calls}=fixture(t);
  get('motion-mode').value='smooth';get('motion-mode').listeners.change();
  await get('motion-execute').listeners.click();
  assert.equal(calls.length,1);
  assert.equal(calls[0].body.execution_mode,'smooth');assert.equal(calls[0].body.duration,4);
});

test('a direct move waits for feedback without showing an interpolation percentage',t=>{
  const {get,panel,state}=fixture(t);
  panel.render({...state,control:{...state.control,running:true,motion_id:'m',name:'张开手',status:'settling',execution_mode:'direct',effective_duration:0,progress:0}});
  assert.equal(get('motion-progress').hidden,true);
  assert.match(get('motion-detail').textContent,/等待到位反馈/);
  assert.doesNotMatch(get('motion-detail').textContent,/渐变|%/);
  assert.equal(get('motion-execute').disabled,true);
});
