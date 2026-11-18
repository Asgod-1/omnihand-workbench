import test from 'node:test';
import assert from 'node:assert/strict';
import {MotionPanel} from '../src/omnihand_usb/web/motion-panel.js';

// Exercise the panel's controls without opening a browser or device.
class Element{
  constructor(tag='div'){this.tag=tag;this.value='';this.children=[];this.listeners={};this.dataset={};this.hidden=false;this.disabled=false;this.classList={toggle(){}};}
  addEventListener(name,handler){this.listeners[name]=handler;}
  setAttribute(){}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  querySelector(tag){return this.children.find(c=>c.tag===tag);}
  querySelectorAll(){return this.children.filter(c=>c.tag==='input');}
}
function fixture(t){
  const elements=new Map(),get=id=>{
    if(!elements.has(id))elements.set(id,new Element());
    return elements.get(id);
  };
  get('motion-mode').value='direct';get('motion-duration').value='4';
  get('control-mode').value='position';get('mixed-current').value='100';get('mixed-velocity').value='500';
  const previous={document:globalThis.document,window:globalThis.window,CustomEvent:globalThis.CustomEvent};
  globalThis.document={getElementById:get,createElement:tag=>new Element(tag),querySelectorAll:selector=>selector==='#axis-controls input'?get('axis-controls').children.flatMap(row=>row.querySelectorAll()):get('motion-presets').children};
  const events=[];globalThis.window={dispatchEvent(event){events.push(event);}};globalThis.CustomEvent=class{constructor(type,options){this.type=type;this.detail=options.detail;}};
  t.after(()=>Object.assign(globalThis,previous));
  const calls=[],panel=new MotionPanel(async(path,body)=>{calls.push({path,body});return {};},async()=>{});
  panel.session='demo-session';panel.target=Array(10).fill(1200);
  const state={connected:true,mode:'demo',positions:Array(10).fill(1100),positions_at:new Date().toISOString(),control:{session_id:panel.session,available:true,armed:true,running:false,power_state:1,capabilities:['position','power','joint','pt','pvt','mixed_joint']}};
  t.after(()=>panel.cancelJog());
  panel.render(state);
  return {get,panel,state,calls,events};
}

test('default button sends exactly one complete target without a duration',async t=>{
  const {get,panel,calls}=fixture(t);
  get('motion-duration').value='invalid';panel.render(panel.state);
  assert.equal(get('motion-duration-field').hidden,true);
  assert.equal(get('motion-execute').disabled,false);
  await get('motion-execute').listeners.click();
  assert.equal(calls.length,1);
  assert.deepEqual(calls[0].body,{action:'move',session_id:'demo-session',execution_mode:'direct',control_mode:'position',positions:Array(10).fill(1200)});
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

test('dragging one slider sends only that axis and keeps the feedback readout',async t=>{
  const {get,panel,calls,events}=fixture(t),row=get('axis-controls').children[3],range=row.children[1];
  range.value='2345';range.listeners.input();await panel.flushJog();
  assert.deepEqual(calls.map(c=>c.body),[{action:'joint',session_id:'demo-session',index:4,position:2345,control_mode:'position'}]);
  assert.equal(row.querySelector('output').textContent,'反馈 1100');
  assert.equal(events.length,0);
});

test('rapid drag updates coalesce to the newest value behind an in-flight request',async t=>{
  const {panel}=fixture(t),sent=[];let resolveFirst;
  panel.api=async(path,body)=>{sent.push(body);if(sent.length===1)await new Promise(resolve=>resolveFirst=resolve);return {};};
  panel.queueJoint(1,1200);const first=panel.flushJog();
  panel.queueJoint(1,1300);panel.queueJoint(1,1400);panel.queueJoint(2,1500);
  assert.equal(sent.length,1);resolveFirst();await first;await panel.flushJog();await panel.flushJog();
  assert.deepEqual(sent.map(b=>[b.index,b.position]),[[1,1200],[1,1400],[2,1500]]);
});

test('stop clears unsent slider targets even when an older request is still in flight',async t=>{
  const {panel}=fixture(t),sent=[];let resolveFirst;
  panel.api=async(path,body)=>{sent.push(body);if(body.action==='joint')await new Promise(resolve=>resolveFirst=resolve);return {};};
  panel.queueJoint(1,1200);const first=panel.flushJog();panel.queueJoint(1,1800);
  await panel.send('stop');resolveFirst();await first;await panel.flushJog();
  assert.deepEqual(sent.map(b=>b.action),['joint','stop']);assert.equal(panel.jogTargets.size,0);
});

test('mixed control uses explicit current units and selection alone sends nothing',async t=>{
  const {get,panel,calls}=fixture(t);
  get('motion-mode').value='smooth';get('control-mode').value='pvt';get('control-mode').listeners.change();
  assert.equal(calls.length,0);assert.equal(get('motion-mode').value,'direct');
  await panel.send('move');
  assert.equal(calls[0].body.control_mode,'pvt');assert.deepEqual(calls[0].body.currents_ma,Array(10).fill(100));
  assert.deepEqual(calls[0].body.velocities,Array(10).fill(500));
});

test('locked controls cannot transmit slider values and reconnect drops pending input',async t=>{
  const {panel,state,calls}=fixture(t);
  panel.render({...state,control:{...state.control,armed:false}});panel.queueJoint(2,1234);await panel.flushJog();
  assert.equal(calls.length,0);
  panel.render(state);panel.queueJoint(2,1700);panel.catalog=async()=>{};
  panel.render({...state,control:{...state.control,session_id:'new-session'}});await panel.flushJog();
  assert.equal(calls.length,0);
});
