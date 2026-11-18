import * as THREE from 'three';
import { OrbitControls } from '/assets/three/OrbitControls.js';
import { STLLoader } from '/assets/three/STLLoader.js';
import { cameraDistance,shortestAngle,easeInOut } from '/view-math.js';
import { SurfaceTaxels } from '/taxel-surface.js';

const host=document.getElementById('hand-viewport');
const status=document.getElementById('model-status');
const label=document.getElementById('model-selection');
let payload=null, model=null, view='palm', selected='ALL', wireframe=false;
const joints=new Map(), meshes=[], links=new Map();
const names={ALL:'整手',THUMB:'拇指',INDEX:'食指',MIDDLE:'中指',RING:'无名指',LITTLE:'小指',PALM:'手心',DORSUM:'手背'};
const regionFor=name=>name.includes('thumb')?'THUMB':name.includes('index')?'INDEX':name.includes('middle')?'MIDDLE':name.includes('ring')?'RING':name.includes('pinky')?'LITTLE':'PALM';
const vec=v=>new THREE.Vector3(...v);
function origin(object, item){object.position.set(...item.xyz);object.rotation.set(...item.rpy,'ZYX');}
let renderer,scene,camera,controls,root,center,size,referenceDistance;
let modelReady=false,poseFitted=false,cameraInitialized=false,animation=null,grid=null,lastPoseSequence=-1;
let taxels=null,pointPinned=false;
const directions={palm:new THREE.Vector3(1,.13,-.16).normalize(),back:new THREE.Vector3(-1,.13,-.16).normalize()};

function bounds(direction){
  root.updateMatrixWorld(true);
  const box=new THREE.Box3().setFromObject(root);
  center=box.getCenter(new THREE.Vector3());size=box.getSize(new THREE.Vector3());
  const forward=(direction||camera.position.clone().sub(controls.target)).clone().normalize();
  if(forward.lengthSq()===0)forward.copy(directions[view]);
  const right=new THREE.Vector3().crossVectors(camera.up,forward).normalize(),up=new THREE.Vector3().crossVectors(forward,right).normalize();
  const span=axis=>Math.abs(axis.x)*size.x+Math.abs(axis.y)*size.y+Math.abs(axis.z)*size.z;
  referenceDistance=cameraDistance({x:span(right),y:span(up),z:span(forward)},camera.fov,camera.aspect,1.08);
  host.dataset.fitDistance=referenceDistance.toFixed(5);
  if(grid)grid.position.set(center.x,box.min.y-.006,center.z);
  return box;
}
function moveCamera(direction,radius,target,animate){
  // Clear residual orbit damping before recording the animation's start state.
  controls.enableDamping=false;controls.update();controls.enableDamping=true;
  const start=new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
  const finish=new THREE.Spherical().setFromVector3(direction.clone().multiplyScalar(radius));
  const fromTarget=controls.target.clone();
  if(animate && cameraInitialized){
    animation={start,finish,delta:shortestAngle(finish.theta-start.theta),fromTarget,target:target.clone(),at:performance.now(),duration:680};
    host.dataset.cameraAnimating='true';
  }else{
    animation=null;controls.target.copy(target);camera.position.copy(target).add(direction.clone().multiplyScalar(radius));
    camera.lookAt(target);controls.update();host.dataset.cameraAnimating='false';
  }
  cameraInitialized=true;
  host.dataset.orbitCenter=[target.x,target.y,target.z].map(v=>v.toFixed(5)).join(',');
}
function fitModel(animate=true){
  if(!modelReady)return;
  const direction=cameraInitialized?camera.position.clone().sub(controls.target).normalize():directions[view];
  bounds(direction);
  moveCamera(direction,referenceDistance,center,animate);
}
function setView(value,animate=true){
  if(!modelReady)return;
  view=value;
  bounds(directions[value]);
  moveCamera(directions[value],cameraInitialized?controls.getDistance():referenceDistance,center,animate);
  document.querySelectorAll('[data-camera]').forEach(b=>{b.classList.toggle('active',b.dataset.camera===value);b.setAttribute('aria-pressed',String(b.dataset.camera===value));});
}
function select(region,notify=true){
  selected=region;
  document.getElementById('model-region').value=region;
  if(region==='PALM' && view!=='palm')setView('palm');
  if(region==='DORSUM' && view!=='back')setView('back');
  paint();
  if(notify)window.dispatchEvent(new CustomEvent('omnihand-region',{detail:region}));
}
function paint(){
  if(!model)return;
  const data=payload?.tactile||{};
  const useHeat=document.getElementById('model-color').value==='tactile';
  for(const mesh of meshes){
    const region=mesh.userData.region==='PALM'?(selected==='DORSUM'||view==='back'?'DORSUM':'PALM'):mesh.userData.region;
    mesh.material.color.set(mesh.userData.baseColor);
    const highlighted=selected!=='ALL' && selected===region;
    mesh.material.emissive.set(highlighted?'#173254':'#000000');
    mesh.material.emissiveIntensity=highlighted ? .10 : 0;
    mesh.material.wireframe=wireframe;
    mesh.material.opacity=1;mesh.material.transparent=false;
  }
  const details=taxels&&payload?taxels.update(data,payload.state,{mode:payload.mode,maximum:payload.maximum,visible:useHeat}):null;
  const tooltip=document.getElementById('taxel-tooltip'),point=taxels?.selection;
  if(!tooltip.hidden&&point?.source===payload?.mode&&tooltip.lastChild)tooltip.lastChild.textContent=`${point.source==='raw'?'原始':'普通'}触觉值：${data[point.region]?.values?.[point.index]??'--'}`;
  label.textContent=selected==='ALL'?'18 个模型部件 · 10 个驱动轴':`${names[selected]} · ${data[selected]?.values?.length||0} 个独立触点`;
  document.getElementById('taxel-count').textContent=details?.count?`${details.count} 个${payload.mode==='raw'?'原始':'普通'}触点 · 非零 ${details.activeCount} · ${details.freshCount?(payload.state.mode==='demo'?'模拟':'实时'):'已过期'}`:'等待逐触点数据';
}
function update(){
  if(!payload||!model)return;
  const s=payload.state;
  const pose=s.pose;
  if(s.sequence<lastPoseSequence)poseFitted=false;
  lastPoseSequence=s.sequence;
  if(s.device?.hand==='left'){
    root.visible=false;status.textContent='当前资源为右手模型，左手连接不套用右手姿态';return;
  }
  root.visible=true;
  if(pose?.joints_rad && !pose.error){
    for(const [name,angle] of Object.entries(pose.joints_rad)){
      const joint=joints.get(name);
      if(joint && Number.isFinite(angle))joint.group.quaternion.setFromAxisAngle(joint.axis,angle);
    }
    if(modelReady && !poseFitted){poseFitted=true;fitModel(false);}
  }
  const fresh=s.connected && Date.now()-Date.parse(s.positions_at)<3000,simulated=s.mode==='demo';
  status.textContent=fresh && pose?.joints_rad?`${simulated?'模拟反馈':'实时反馈'} → 官方关节解算`:!s.connected?(pose?.joints_rad?'已断开 · 保留最后姿态':'未连接 · 模型零位'):pose?.error||'等待新的关节反馈';
  host.dataset.poseMode=simulated?'simulated':'measured';
  status.classList.toggle('live',!!fresh && !!pose?.joints_rad);
  const clipped=pose?.clamped_for_display||[];
  document.getElementById('pose-note').textContent=`模型始终使用${simulated?'模拟设备':'实机'}反馈，目标编辑不改变三维姿态。被动关节由官方求解器计算。`+(clipped.length?`轴 ${clipped.join('、')} 轻微越界，仅三维输入限幅；原始反馈保留。`:'');
  paint();
}

async function initialize(){
  try{
    renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
    renderer.outputColorSpace=THREE.SRGBColorSpace;
    renderer.toneMapping=THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure=1.3;
    renderer.setClearColor(0x0e1b30,0);
    renderer.domElement.setAttribute('aria-label','官方 OmniHand 2025 右手三维模型，可拖动旋转、滚轮缩放');
    host.prepend(renderer.domElement);
    scene=new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xc8ddff,0x24364b,2.5));
    const key=new THREE.DirectionalLight(0xffffff,3.0);key.position.set(-.3,.4,.3);scene.add(key);
    const rim=new THREE.DirectionalLight(0x7baaff,2);rim.position.set(.3,.1,-.15);scene.add(rim);
    camera=new THREE.PerspectiveCamera(33,1,.001,10);
    controls=new OrbitControls(camera,renderer.domElement);
    controls.enableDamping=true;controls.dampingFactor=.12;
    controls.minDistance=.1;controls.maxDistance=.9;
    controls.enablePan=false;
    controls.addEventListener('start',()=>{animation=null;host.dataset.cameraAnimating='false';});
    const response=await fetch('/assets/model/model.json');
    if(!response.ok)throw new Error('请先运行 scripts/setup_visualization.py 安装官方模型');
    model=await response.json();
    root=new THREE.Group();root.rotation.x=-Math.PI/2;scene.add(root);
    const loader=new STLLoader();
    for(const link of model.links){const group=new THREE.Group();group.name=link.name;links.set(link.name,group);}
    await Promise.all(model.links.map(async link=>{
      const group=links.get(link.name);
      await Promise.all(link.visuals.map(async visual=>{
        const geometry=await loader.loadAsync('/assets/model/'+encodeURIComponent(visual.mesh));
        geometry.computeVertexNormals();
        const jointPart=/abad|roll/.test(link.name);
        const baseColor=jointPart?'#7288a8':'#cad6e9';
        const material=new THREE.MeshStandardMaterial({color:baseColor,metalness:.32,roughness:.42,side:THREE.DoubleSide});
        const mesh=new THREE.Mesh(geometry,material);
        mesh.name=link.name;mesh.userData={region:regionFor(link.name),baseColor};
        origin(mesh,visual);mesh.scale.set(...visual.scale);group.add(mesh);meshes.push(mesh);
      }));
    }));
    const children=new Set();
    for(const joint of model.joints){
      const fixed=new THREE.Group();origin(fixed,joint);
      const moving=new THREE.Group();fixed.add(moving);
      moving.add(links.get(joint.child));links.get(joint.parent).add(fixed);children.add(joint.child);
      joints.set(joint.name,{group:moving,axis:vec(joint.axis).normalize()});
    }
    for(const [name,link] of links)if(!children.has(name))root.add(link);
    camera.aspect=Math.max(1,host.clientWidth)/Math.max(1,host.clientHeight);camera.updateProjectionMatrix();
    taxels=new SurfaceTaxels(meshes,host);
    modelReady=true;update();
    grid=new THREE.GridHelper(.34,17,0x385372,0x1e344c);grid.material.transparent=true;grid.material.opacity=.35;scene.add(grid);
    fitModel(false);setView('palm',false);update();
    document.getElementById('model-loading').hidden=true;
    const observer=new ResizeObserver(()=>{
      const width=host.clientWidth,height=host.clientHeight;
      renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();
      fitModel(false);
      renderer.render(scene,camera); // Keep the resized canvas populated before the next animation frame.
    });observer.observe(host);
    const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();
    let down=null,lastMove=0;
    const tooltip=document.getElementById('taxel-tooltip');
    function aim(event){
      const rect=renderer.domElement.getBoundingClientRect();pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,camera);
    }
    const pickPoint=()=>taxels.pick(raycaster,{camera,pointer,width:host.clientWidth,height:host.clientHeight});
    function inspectPoint(hit,event,pinned){
      pointPinned=pinned;taxels.select({...hit,pinned});
      window.dispatchEvent(new CustomEvent('omnihand-point',{detail:{region:hit.region,index:hit.index,source:hit.source,pinned}}));
      tooltip.replaceChildren();const title=document.createElement('strong');title.textContent=`${hit.name} #${hit.index}`;
      tooltip.append(title,document.createElement('br'),`${hit.source==='raw'?'原始':'普通'}触觉值：${hit.value}`);
      const rect=host.getBoundingClientRect();tooltip.style.left=Math.max(8,Math.min(host.clientWidth-180,event.clientX-rect.left+14))+'px';tooltip.style.top=Math.max(8,Math.min(host.clientHeight-65,event.clientY-rect.top+14))+'px';tooltip.hidden=false;
    }
    renderer.domElement.addEventListener('pointerdown',event=>{down=[event.clientX,event.clientY];});
    renderer.domElement.addEventListener('pointerup',event=>{
      const start=down;down=null;if(!start||Math.hypot(event.clientX-start[0],event.clientY-start[1])>5)return;
      aim(event);const point=pickPoint();if(point){inspectPoint(point,event,true);return;}
      pointPinned=false;taxels.select(null);tooltip.hidden=true;
      const hit=raycaster.intersectObjects(meshes)[0];
      if(hit)select(hit.object.userData.region==='PALM'&&view==='back'?'DORSUM':hit.object.userData.region);
    });
    renderer.domElement.addEventListener('pointermove',event=>{
      if(down||pointPinned||performance.now()-lastMove<90)return;lastMove=performance.now();aim(event);
      const hit=pickPoint();if(hit)inspectPoint(hit,event,false);else tooltip.hidden=true;
    });
    renderer.domElement.addEventListener('pointerleave',()=>{down=null;if(!pointPinned)tooltip.hidden=true;});
    renderer.setAnimationLoop(()=>{
      if(animation){
        const a=animation,t=Math.min(1,(performance.now()-a.at)/a.duration),e=easeInOut(t);
        const spherical=new THREE.Spherical(THREE.MathUtils.lerp(a.start.radius,a.finish.radius,e),THREE.MathUtils.lerp(a.start.phi,a.finish.phi,e),a.start.theta+a.delta*e);
        controls.target.copy(a.fromTarget).lerp(a.target,e);camera.position.setFromSpherical(spherical).add(controls.target);
        camera.lookAt(controls.target);controls.enableDamping=false;controls.update();controls.enableDamping=true;
        if(t===1){animation=null;host.dataset.cameraAnimating='false';}
      }else controls.update();
      renderer.render(scene,camera);
    });
    window.addEventListener('pagehide',()=>{renderer.setAnimationLoop(null);controls.dispose();taxels.dispose();renderer.dispose();});
  }catch(error){
    status.textContent='三维模型加载失败';
    const message=document.getElementById('model-loading');message.hidden=false;message.textContent=error.message;
  }
}
window.addEventListener('omnihand-state',event=>{payload=event.detail;update();});
window.addEventListener('tactile-region-selected',event=>select(event.detail,false));
window.addEventListener('tactile-point-selected',event=>{
  const p=event.detail;if(!taxels||p.source!==payload?.mode)return;
  pointPinned=!!p.pinned;taxels.select(p);selected=p.region;document.getElementById('model-region').value=p.region;
  if(p.focus){if(p.region==='DORSUM')setView('back');else setView('palm');}
  paint();
});
document.querySelectorAll('[data-camera]').forEach(button=>button.addEventListener('click',()=>{setView(button.dataset.camera);paint();}));
document.getElementById('model-region').addEventListener('change',event=>select(event.target.value));
document.getElementById('model-color').addEventListener('change',paint);
document.getElementById('model-fit').addEventListener('click',()=>fitModel(true));
document.getElementById('model-wireframe').addEventListener('click',event=>{wireframe=!wireframe;event.currentTarget.classList.toggle('active',wireframe);event.currentTarget.setAttribute('aria-pressed',String(wireframe));paint();});
initialize();
