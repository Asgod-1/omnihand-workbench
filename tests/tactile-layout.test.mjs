import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
async function moduleAt(path){const source=await readFile(new URL(path,import.meta.url),'utf8');return import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));}
const {tactileLayout,tactileColor,tactileStatistics,TOOLBOX_RAW_LAYOUTS,RAW_COUNTS}=await moduleAt('../src/omnihand_usb/web/tactile-layout.js');
const {shortestAngle,cameraDistance,easeInOut}=await moduleAt('../src/omnihand_usb/web/view-math.js');

test('right and left finger maps preserve the official top and bottom indices',()=>{
  const right=tactileLayout('INDEX',16),left=tactileLayout('INDEX',16,{side:'left'});
  const row=(map,n)=>map.points.filter(p=>p.row===n).sort((a,b)=>a.column-b.column).map(p=>p.index);
  assert.deepEqual(row(right,0),[14,15]);assert.deepEqual(row(right,7),[0,1]);
  assert.deepEqual(row(left,0),[15,14]);assert.deepEqual(row(left,7),[1,0]);
  assert.equal(new Set(right.points.map(p=>p.index)).size,16);
  const table=tactileLayout('INDEX',16,{physical:false});
  assert.equal(table.points[14].row,3);assert.equal(table.points[14].column,2);
});
test('official raw topology preserves all 268 unique indices and irregular palm/back cells',()=>{
  for(const [name,count]of Object.entries(RAW_COUNTS)){
    const map=tactileLayout(name,count);assert.equal(map.kind,'toolbox-relative');assert.equal(map.source.toolbox_version,'2.7.2');
    assert.equal(map.points.length,count);assert.equal(new Set(map.points.map(p=>p.index)).size,count);
    assert.equal(new Set(map.points.map(p=>p.row+':'+p.column)).size,count);
    assert.deepEqual(map.points.map(p=>[p.row,p.column]),TOOLBOX_RAW_LAYOUTS.right[name].pts);
  }
  const palm=tactileLayout('PALM',78),back=tactileLayout('DORSUM',102);
  assert.deepEqual([palm.points[0].row,palm.points[0].column],[13,3]);
  assert.deepEqual([palm.points[77].row,palm.points[77].column],[0,3]);
  assert.deepEqual([back.points[0].row,back.points[0].column],[10,3]);
  assert.deepEqual([back.points[101].row,back.points[101].column],[0,7]);
  assert.ok(palm.points.length<palm.rows*palm.columns);assert.ok(back.points.length<back.rows*back.columns);
  assert.equal(tactileLayout('PALM',25).kind,'index');
});

test('raw finger zero is proximal and the final pair is distal for both hands',()=>{
  for(const side of ['right','left'])for(const [name,count]of Object.entries(RAW_COUNTS).slice(0,5)){
    const map=tactileLayout(name,count,{side});
    assert.equal(map.points[0].v,0);assert.equal(map.points[count-1].v,1);
    assert.equal(map.points[0].column,side==='right'?0:1);
  }
});
test('all-zero contact data remains valid, including index zero',()=>{
  assert.deepEqual(tactileStatistics([0,0,0]),{maximum:0,minimum:0,mean:0,peakIndex:0,active:0});
  assert.equal(tactileStatistics(null),null);assert.match(tactileColor(0,0),/^#[0-9a-f]{6}$/);
});
test('camera uses a short orbit and fits a narrower viewport at a larger distance',()=>{
  assert.ok(Math.abs(shortestAngle(350*Math.PI/180)-(-10*Math.PI/180))<1e-9);
  assert.ok(cameraDistance({x:.1,y:.2,z:.08},33,.3)>cameraDistance({x:.1,y:.2,z:.08},33,1.5));
  assert.equal(cameraDistance({x:.1,y:.2,z:.08},33,.6),cameraDistance({x:.1,y:.2,z:.08},33,1.5));
  const tangent=Math.tan(33*Math.PI/360);
  for(const aspect of [.3,.6,1.5]){const d=cameraDistance({x:.1,y:.2,z:.08},33,aspect);assert.ok(.05/(d-.04)<=tangent*aspect);assert.ok(.1/(d-.04)<=tangent);}
  assert.equal(easeInOut(0),0);assert.equal(easeInOut(1),1);assert.equal(easeInOut(.5),.5);
});
