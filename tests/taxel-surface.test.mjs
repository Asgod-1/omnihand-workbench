import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=new URL('../',import.meta.url),threePath=new URL('vendor/web_assets/three/three.module.js',root),modelPath=new URL('vendor/web_assets/model/model.json',root);
const available=existsSync(threePath)&&existsSync(modelPath);
let THREE,SurfaceTaxels,RAW_COUNTS,model,layer,host,meshes,joints,scene;
if(available){
  THREE=await import(threePath.href);
  const layoutURL=new URL('src/omnihand_usb/web/tactile-layout.js',root).href;
  ({RAW_COUNTS}=await import(layoutURL));
  let source=await readFile(new URL('src/omnihand_usb/web/taxel-surface.js',root),'utf8');
  source=source.replace("from 'three'",`from '${threePath.href}'`).replace("from '/tactile-layout.js'",`from '${layoutURL}'`);
  ({SurfaceTaxels}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64')));
  model=JSON.parse(await readFile(modelPath,'utf8'));
  const links=new Map(model.links.map(l=>[l.name,new THREE.Group()]));meshes=[];joints=new Map();scene=new THREE.Group();
  const used=new Set(['R_palm','R_thumb_dip_link','R_index_dip_link','R_middle_dip_link','R_ring_dip_link','R_pinky_dip_link']);
  for(const link of model.links){
    links.get(link.name).name=link.name;
    if(!used.has(link.name))continue;
    const v=link.visuals[0],buffer=await readFile(new URL('vendor/web_assets/model/'+v.mesh,root));
    const count=buffer.readUInt32LE(80),positions=new Float32Array(count*9);
    for(let i=0;i<count;i++)for(let j=0;j<9;j++)positions[i*9+j]=buffer.readFloatLE(84+i*50+12+j*4);
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));geometry.computeVertexNormals();
    const mesh=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({side:THREE.DoubleSide}));mesh.name=link.name;mesh.position.set(...v.xyz);mesh.rotation.set(...v.rpy,'ZYX');
    links.get(link.name).add(mesh);meshes.push(mesh);
  }
  const children=new Set();
  for(const j of model.joints){const fixed=new THREE.Group(),moving=new THREE.Group();fixed.position.set(...j.xyz);fixed.rotation.set(...j.rpy,'ZYX');fixed.add(moving);moving.add(links.get(j.child));links.get(j.parent).add(fixed);children.add(j.child);joints.set(j.name,{group:moving,axis:new THREE.Vector3(...j.axis)});}
  for(const [name,link]of links)if(!children.has(name))scene.add(link);
  host={dataset:{}};layer=new SurfaceTaxels(meshes,host);
}
const measured=()=>Object.fromEntries(Object.entries(RAW_COUNTS).map(([name,count])=>[name,{values:Array.from({length:count},(_,i)=>i),sampled_at:new Date().toISOString()}]));
const state=()=>({connected:true,device:{hand:'right'},raw_tactile:{sampled_at:new Date().toISOString()}});

test('all 268 official indices have unique finite locations on the official STL surface',{skip:!available},()=>{
  const result=layer.update(measured(),state(),{mode:'raw',maximum:255});
  assert.equal(result.count,268);assert.equal(result.rayHits+result.fallbacks,268);assert.equal(host.dataset.taxelCount,'268');
  for(const group of layer.groups){
    assert.equal(group.points.parent,group.mesh);
    const locations=new Set();
    for(const p of group.placement){assert.ok(p.position.toArray().every(Number.isFinite));assert.ok(Math.abs(p.normal.length()-1)<1e-5);locations.add(p.position.toArray().map(x=>x.toFixed(7)).join(','));}
    assert.equal(locations.size,RAW_COUNTS[group.region],group.region+' must not collapse two sensor indices onto one vertex');
  }
  assert.ok(result.rayHits>=250,'Most cells must project directly, without boundary fallback');
});

test('taxels remain on the textured fingertip caps and the rear sensing pad',{skip:!available},()=>{
  for(const group of layer.groups){
    for(const p of group.placement){
      assert.equal(p.method,'ray-surface',group.region+' must not fall back onto a mechanical vertex');
      if(['INDEX','MIDDLE','RING','LITTLE'].includes(group.region))assert.ok(p.position.z>.020,'No taxel on fingertip hinge');
      if(group.region==='THUMB')assert.ok(p.position.x>.013,'Thumb taxel stays on the printed cap');
      if(group.region==='DORSUM'){
        assert.ok(p.position.x<-.034);
        assert.ok(Math.hypot(p.position.y,p.position.z-.06333)<.0237,'Rear taxel stays inside the sensing disk');
      }
    }
  }
});

test('finger taxels follow the real URDF joint while the dorsal patch stays on the palm',{skip:!available},()=>{
  scene.updateMatrixWorld(true);
  const finger=layer.groups.find(g=>g.region==='INDEX'),back=layer.groups.find(g=>g.region==='DORSUM');
  const point=object=>{const m=new THREE.Matrix4();object.getMatrixAt(0,m);return object.localToWorld(new THREE.Vector3().setFromMatrixPosition(m));};
  const before=point(finger.points),backBefore=point(back.points);
  const joint=joints.get('R_index_pip_joint');joint.group.quaternion.setFromAxisAngle(joint.axis,.8);scene.updateMatrixWorld(true);
  assert.ok(before.distanceTo(point(finger.points))>.01);assert.ok(backBefore.distanceTo(point(back.points))<1e-9);
  joint.group.quaternion.identity();scene.updateMatrixWorld(true);
});

test('a visible index-zero taxel can be picked, including a small screen offset',{skip:!available},()=>{
  scene.updateMatrixWorld(true);
  const group=layer.groups.find(g=>g.region==='INDEX'),p=group.placement[0];
  const world=group.mesh.localToWorld(p.position.clone().addScaledVector(p.normal,.00055));
  const normal=p.normal.clone().transformDirection(group.mesh.matrixWorld),camera=new THREE.PerspectiveCamera(35,800/600,.001,10);
  camera.up.set(0,0,1);camera.position.copy(world).addScaledVector(normal,.22);camera.lookAt(world);camera.updateMatrixWorld(true);
  const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2(0,0);raycaster.setFromCamera(pointer,camera);
  const direct=layer.pick(raycaster);assert.equal(direct?.region,'INDEX');assert.equal(direct?.index,0);
  pointer.set(.015,0);raycaster.setFromCamera(pointer,camera);
  const near=layer.pick(raycaster,{camera,pointer,width:800,height:600});assert.equal(near?.region,'INDEX');assert.equal(near?.index,0);
});

test('sensor footprints remain separate and contact fill grows without moving its centre',{skip:!available},()=>{
  for(const g of layer.groups){
    const rx=g.spec.radiusLong||g.spec.radius,ry=g.spec.radiusCross||g.spec.radius;
    for(let i=0;i<g.placement.length;i++)for(let j=i+1;j<g.placement.length;j++){
      const a=g.placement[i].position,b=g.placement[j].position;
      const distance=Math.hypot((a.getComponent(g.spec.longAxis)-b.getComponent(g.spec.longAxis))/rx,(a.getComponent(g.spec.crossAxis)-b.getComponent(g.spec.crossAxis))/ry);
      assert.ok(distance>g.footprints[i]+g.footprints[j],g.region+' has distinct sensor outlines');
    }
  }
  const data=measured();for(const r of Object.values(data))r.values.fill(0);
  assert.equal(layer.update(data,state(),{mode:'raw',maximum:255}).activeCount,0);
  const finger=layer.groups.find(g=>g.region==='INDEX'),before=new THREE.Matrix4(),after=new THREE.Matrix4();
  finger.points.getMatrixAt(0,before);data.INDEX.values[0]=255;
  assert.equal(layer.update(data,state(),{mode:'raw',maximum:255}).activeCount,1);finger.points.getMatrixAt(0,after);
  assert.ok(new THREE.Vector3().setFromMatrixPosition(before).distanceTo(new THREE.Vector3().setFromMatrixPosition(after))<1e-9);
  assert.ok(new THREE.Vector3().setFromMatrixScale(after).x>new THREE.Vector3().setFromMatrixScale(before).x*4);
});

test('disconnection preserves point identity but never marks old values as live',{skip:!available},()=>{
  const data=measured(),result=layer.update(data,{...state(),connected:false},{mode:'raw',maximum:255});
  assert.equal(result.count,268);assert.equal(result.freshCount,0);assert.equal(host.dataset.taxelFresh,'0');
  assert.equal(data.DORSUM.values[101],101);
  layer.dispose();for(const mesh of meshes){mesh.geometry.dispose();mesh.material.dispose();}
});
