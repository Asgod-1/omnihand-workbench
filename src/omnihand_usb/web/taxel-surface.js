import * as THREE from 'three';
import {REGIONS,REGION_NAMES,tactileLayout,tactileColor} from '/tactile-layout.js';

// Visual registration regions in URDF mesh coordinates (metres).
// The official ToolBox supplies index topology, not these metric coordinates.
// Project onto the actual mesh; a physical contact calibration can refine ROIs.
export const SURFACE_REGISTRATION={
  THUMB:{link:'R_thumb_dip_link',normalAxis:2,sign:1,longAxis:0,crossAxis:1,long:[.002,.021],cross:[-.004,.008],radius:.00145},
  INDEX:{link:'R_index_dip_link',normalAxis:0,sign:1,longAxis:2,crossAxis:1,long:[.003,.034],cross:[-.0044,.0044],radius:.00165},
  MIDDLE:{link:'R_middle_dip_link',normalAxis:0,sign:1,longAxis:2,crossAxis:1,long:[.003,.034],cross:[-.0044,.0044],radius:.00165},
  RING:{link:'R_ring_dip_link',normalAxis:0,sign:1,longAxis:2,crossAxis:1,long:[.003,.034],cross:[-.0044,.0044],radius:.00165},
  LITTLE:{link:'R_pinky_dip_link',normalAxis:0,sign:1,longAxis:2,crossAxis:1,long:[.003,.034],cross:[-.0044,.0044],radius:.00165},
  PALM:{link:'R_palm',normalAxis:0,sign:1,longAxis:2,crossAxis:1,long:[.020,.092],cross:[-.035,.030],radius:.00155},
  DORSUM:{link:'R_palm',normalAxis:0,sign:-1,longAxis:2,crossAxis:1,long:[.022,.087],cross:[-.034,.032],radius:.00185}
};

function targetFor(point,spec){
  const p=new THREE.Vector3();
  p.setComponent(spec.longAxis,THREE.MathUtils.lerp(...spec.long,point.v));
  p.setComponent(spec.crossAxis,THREE.MathUtils.lerp(...spec.cross,point.u));
  return p;
}

export function projectTaxelLayout(geometry,layout,spec){
  geometry.computeBoundingBox();
  const box=geometry.boundingBox,proxy=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({side:THREE.DoubleSide}));
  proxy.updateMatrixWorld(true);
  const raycaster=new THREE.Raycaster(),direction=new THREE.Vector3().setComponent(spec.normalAxis,-spec.sign);
  const normalReference=new THREE.Vector3().setComponent(spec.normalAxis,spec.sign);
  const positions=geometry.getAttribute('position');
  const result=layout.points.map(point=>{
    const target=targetFor(point,spec),origin=target.clone();
    origin.setComponent(spec.normalAxis,(spec.sign>0?box.max:box.min).getComponent(spec.normalAxis)+spec.sign*.03);
    raycaster.set(origin,direction);
    const hit=raycaster.intersectObject(proxy,false)[0];
    if(hit){
      const normal=hit.face.normal.clone();if(normal.dot(normalReference)<0)normal.negate();
      return {index:point.index,position:hit.point.clone(),normal,method:'ray-surface'};
    }
    // Holes or curved boundaries: use an actual surface vertex, never a point
    // floating on the guessed registration plane. This is marked in metadata.
    let best=Infinity,chosen=null;
    for(let i=0;i<positions.count;i++){
      const p=new THREE.Vector3().fromBufferAttribute(positions,i);
      const score=(p.getComponent(spec.longAxis)-target.getComponent(spec.longAxis))**2+
        (p.getComponent(spec.crossAxis)-target.getComponent(spec.crossAxis))**2;
      const facing=spec.sign*p.getComponent(spec.normalAxis);
      if(score<best-1e-12||(Math.abs(score-best)<1e-12&&chosen&&facing>spec.sign*chosen.getComponent(spec.normalAxis))){best=score;chosen=p;}
    }
    if(!chosen)throw new Error('Model geometry has no surface vertices');
    return {index:point.index,position:chosen,normal:normalReference.clone(),method:'nearest-surface-vertex'};
  });
  proxy.material.dispose();return result;
}

export class SurfaceTaxels{
  constructor(meshes,host){
    this.meshes=meshes;this.host=host;this.groups=[];this.pickables=[];this.signature='';this.selection=null;this.projected=new Map();
    this.circle=new THREE.CircleGeometry(1,18);this.ringGeometry=new THREE.RingGeometry(1.10,1.36,24);
    this.markerMaterial=new THREE.MeshBasicMaterial({vertexColors:false,side:THREE.FrontSide,toneMapped:false,polygonOffset:true,polygonOffsetFactor:-2});
    this.borderMaterial=new THREE.MeshBasicMaterial({color:'#274d70',side:THREE.FrontSide,toneMapped:false});
    this.highlight=new THREE.Mesh(this.ringGeometry,new THREE.MeshBasicMaterial({color:'#ffc666',side:THREE.DoubleSide,toneMapped:false}));
    this.highlight.visible=false;this.dummy=new THREE.Object3D();this.color=new THREE.Color();
  }
  clear(){
    for(const group of this.groups){group.mesh.remove(group.points,group.border);group.points.dispose();group.border.dispose();}
    this.groups=[];this.pickables=[];this.highlight.removeFromParent();this.highlight.visible=false;
  }
  update(data,state,{mode,maximum,visible=true}){
    this.data=data;this.state=state;this.mode=mode;
    const signature=[mode,state.device?.hand,...REGIONS.map(region=>data[region]?.values?.length||0)].join(':');
    if(signature!==this.signature){
      this.clear();this.signature=signature;
      if(state.device?.hand!=='left')for(const region of REGIONS){
        const values=data[region]?.values;if(!values?.length)continue;
        const layout=tactileLayout(region,values.length),spec=SURFACE_REGISTRATION[region];
        if(layout.kind==='index')continue;
        const mesh=this.meshes.find(m=>m.name===spec.link);if(!mesh)continue;
        const cacheKey=region+':'+values.length;
        let placement=this.projected.get(cacheKey);
        if(!placement){placement=projectTaxelLayout(mesh.geometry,layout,spec);this.projected.set(cacheKey,placement);}
        const points=new THREE.InstancedMesh(this.circle,this.markerMaterial,values.length);
        const border=new THREE.InstancedMesh(this.circle,this.borderMaterial,values.length);
        points.instanceMatrix.setUsage(THREE.StaticDrawUsage);points.frustumCulled=false;border.frustumCulled=false;
        points.userData={taxels:true,region,source:mode,placement};points.name=region+'-taxels';border.name=region+'-taxel-borders';
        placement.forEach((p,i)=>{
          this.dummy.position.copy(p.position).addScaledVector(p.normal,.00055);
          this.dummy.quaternion.setFromUnitVectors(new THREE.Vector3(0,0,1),p.normal);
          this.dummy.scale.setScalar(spec.radius);this.dummy.updateMatrix();points.setMatrixAt(i,this.dummy.matrix);
          this.dummy.position.copy(p.position).addScaledVector(p.normal,.00043);
          this.dummy.scale.setScalar(spec.radius*1.14);this.dummy.updateMatrix();border.setMatrixAt(i,this.dummy.matrix);
        });
        mesh.add(border,points);this.groups.push({region,mesh,points,border,placement,spec});this.pickables.push(points);
      }
    }
    let count=0,rayHits=0,fallbacks=0,freshCount=0;
    for(const group of this.groups){
      const entry=data[group.region],values=entry.values;
      const fresh=state.connected&&Date.now()-Date.parse(entry.sampled_at||state.raw_tactile?.sampled_at)<3000;
      group.points.visible=visible;group.border.visible=visible;
      for(let i=0;i<values.length;i++){
        this.color.set(fresh?tactileColor(values[i],maximum):'#728295');group.points.setColorAt(i,this.color);
        count++;if(fresh)freshCount++;
        if(group.placement[i].method==='ray-surface')rayHits++;else fallbacks++;
      }
      if(group.points.instanceColor)group.points.instanceColor.needsUpdate=true;
    }
    Object.assign(this.host.dataset,{taxelCount:String(count),taxelSource:mode,taxelRayHits:String(rayHits),taxelFallbacks:String(fallbacks),taxelFresh:String(freshCount),taxelMapping:'official-index-surface-registration'});
    this.visible=visible;this.refreshSelection();
    return {count,rayHits,fallbacks,freshCount};
  }
  select(selection){this.selection=selection;this.host.dataset.selectedTaxel=selection?`${selection.source}:${selection.region}:${selection.index}`:'';this.refreshSelection();}
  refreshSelection(){
    const p=this.selection,group=this.groups.find(g=>g.region===p?.region),placement=group?.placement[p?.index];
    this.highlight.visible=!!placement&&this.visible&&p.source===this.mode;
    if(!this.highlight.visible)return;
    group.mesh.add(this.highlight);this.highlight.position.copy(placement.position).addScaledVector(placement.normal,.0009);
    this.highlight.quaternion.setFromUnitVectors(new THREE.Vector3(0,0,1),placement.normal);this.highlight.scale.setScalar(group.spec.radius);
  }
  pick(raycaster,screen){
    if(!this.visible)return null;
    const hits=raycaster.intersectObjects(this.pickables,false);
    const nearestSurface=raycaster.intersectObjects(this.meshes,false)[0];
    const hit=hits.find(h=>!nearestSurface||h.distance<=nearestSurface.distance+.0012);
    if(!hit&&screen){
      // Visible circles can be only a few pixels wide. Permit a small screen
      // tolerance while still rejecting taxels hidden behind the hand.
      const cameraPosition=new THREE.Vector3();screen.camera.getWorldPosition(cameraPosition);
      const candidates=[];
      for(const group of this.groups)for(const p of group.placement){
        const world=group.mesh.localToWorld(p.position.clone().addScaledVector(p.normal,.00055));
        const normal=p.normal.clone().transformDirection(group.mesh.matrixWorld);
        if(normal.dot(cameraPosition.clone().sub(world))<=0)continue;
        const projected=world.clone().project(screen.camera);if(projected.z<-1||projected.z>1)continue;
        const pixels=Math.hypot((projected.x-screen.pointer.x)*screen.width/2,(projected.y-screen.pointer.y)*screen.height/2);
        if(pixels<=7)candidates.push({group,p,world,pixels});
      }
      candidates.sort((a,b)=>a.pixels-b.pixels);
      for(const c of candidates.slice(0,3)){
        const check=new THREE.Raycaster(cameraPosition,c.world.clone().sub(cameraPosition).normalize());
        const blocked=check.intersectObjects(this.meshes,false)[0];
        if(!blocked||blocked.distance>=cameraPosition.distanceTo(c.world)-.0012){
          const region=c.group.region,index=c.p.index;
          return {region,index,source:this.mode,value:this.data[region]?.values[index],name:REGION_NAMES[region],point:c.world};
        }
      }
    }
    if(!hit)return null;
    const region=hit.object.userData.region,index=hit.instanceId,entry=this.data[region];
    return {region,index,source:this.mode,value:entry?.values?.[index],name:REGION_NAMES[region],point:hit.point};
  }
  dispose(){this.clear();this.circle.dispose();this.ringGeometry.dispose();this.markerMaterial.dispose();this.borderMaterial.dispose();this.highlight.material.dispose();}
}
