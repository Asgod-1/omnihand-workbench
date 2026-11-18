import {REGIONS,REGION_NAMES,RAW_COUNTS,ORDINARY_COUNTS,tactileLayout,tactileColor,tactileStatistics} from '/tactile-layout.js';
const $=id=>document.getElementById(id),SVG='http://www.w3.org/2000/svg';
const statusNames={ready:'原始已返回',no_response:'原始未返回',partial:'原始不完整',error:'原始读取异常',unavailable:'原始接口未提供',disabled:'原始采集已关闭'};
function svgNode(tag,attrs){const n=document.createElementNS(SVG,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));return n;}

export class TactilePanel{
  constructor(onChange){
    this.onChange=onChange;this.mode='raw';this.region='INDEX';this.point=0;this.pointMode='raw';this.pinned=false;this.cards=new Map();this.dataByMode={raw:{},ordinary:{}};
    for(const mode of ['raw','ordinary'])for(const region of REGIONS){
      const card=document.createElement('section');card.className='taxel-card '+(REGIONS.indexOf(region)<5?'finger-card':'surface-card');
      card.dataset.region=region;card.dataset.source=mode;card.setAttribute('aria-label',`${mode==='raw'?'原始':'普通'}${REGION_NAMES[region]}全部触点`);
      card.innerHTML='<header><h3></h3><span class="card-count"></span></header><div class="taxel-orientation"></div><svg role="group"></svg><p class="card-empty">等待数据</p><footer><span class="card-peak"></span><span class="card-age"></span></footer>';
      card.querySelector('h3').textContent=REGION_NAMES[region];
      card.querySelector('svg').setAttribute('aria-label',`${REGION_NAMES[region]}${mode==='raw'?'原始':'普通'}逐点读数`);
      const destination=mode==='ordinary'?'ordinary-overview':REGIONS.indexOf(region)<5?'raw-fingers':'raw-surfaces';
      $(destination).append(card);this.cards.set(mode+':'+region,{card,signature:null,layout:null});
    }
    for(const id of ['color-scale','show-point-values','show-point-indices'])$(id).addEventListener('change',()=>this.onChange());
    for(const id of ['raw-fingers','raw-surfaces','ordinary-overview']){
      for(const type of ['pointerover','click','focusin'])$(id).addEventListener(type,event=>this.pointEvent(event));
      $(id).addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();this.pointEvent(event);}});
    }
    $('point-lock').addEventListener('click',()=>{this.pinned=!this.pinned;this.inspect();window.dispatchEvent(new CustomEvent('tactile-point-selected',{detail:{region:this.region,index:this.point,source:this.pointMode,pinned:this.pinned,focus:false}}));});
    window.addEventListener('omnihand-point',event=>{
      const p=event.detail;this.region=p.region;this.point=p.index;this.pointMode=p.source;this.pinned=!!p.pinned;this.inspect();
    });
  }
  selectRegion(region){if(!REGIONS.includes(region))return;this.region=region;this.point=0;this.pointMode=this.mode;this.inspect();}
  pointEvent(event){
    const target=event.target.closest('[data-index]');if(!target)return;
    if(this.pinned&&(event.type==='pointerover'||event.type==='focusin'))return;
    this.region=target.dataset.region;this.point=Number(target.dataset.index);this.pointMode=target.dataset.source;
    const focus=event.type==='click'||event.type==='keydown';if(focus)this.pinned=true;
    this.inspect();window.dispatchEvent(new CustomEvent('tactile-point-selected',{detail:{region:this.region,index:this.point,source:this.pointMode,focus,pinned:this.pinned}}));
  }
  inspect(){
    const entry=this.dataByMode[this.pointMode]?.[this.region],values=entry?.values;
    const record=this.cards.get(this.pointMode+':'+this.region),p=record?.layout?.points[this.point];
    $('point-index').textContent=values?.length?`${REGION_NAMES[this.region]} #${this.point}`:'等待触点';
    $('point-value').textContent=values?.[this.point]??'--';
    $('point-position').textContent=p?`${this.pointMode==='raw'?'原始':'普通'} · 第 ${p.row+1} 行 / 第 ${p.column+1} 列`:'悬停任意触点，二维与三维同步定位';
    $('point-time').textContent=entry?.sampled_at?new Date(entry.sampled_at).toLocaleTimeString('zh-CN',{hour12:false}):'--';
    $('point-lock').textContent=this.pinned?'已固定 · 解除':'跟随指针';$('point-lock').setAttribute('aria-pressed',String(this.pinned));
    for(const {card}of this.cards.values())for(const group of card.querySelectorAll('[data-index]'))group.classList.toggle('point-selected',
      group.dataset.region===this.region&&group.dataset.source===this.pointMode&&Number(group.dataset.index)===this.point);
  }
  render(state){
    const raw=state.raw_tactile||{},rawData=raw.status==='ready'?Object.fromEntries(Object.entries(raw.regions||{}).map(([k,v])=>[k,{...v,sampled_at:raw.sampled_at}])):{};
    const ordinaryEnabled=state.mode!=='canfd'&&state.tactile_policy?.ordinary!=='disabled';
    const ordinary=ordinaryEnabled?(state.tactile||{}):{};this.state=state;this.dataByMode={raw:rawData,ordinary};
    $('ordinary-block').hidden=!ordinaryEnabled;
    const rawTotal=Object.values(rawData).reduce((n,e)=>n+(e.values?.length||0),0),ordinaryTotal=Object.values(ordinary).reduce((n,e)=>n+(e.values?.length||0),0);
    this.mode=rawTotal||!ordinaryEnabled?'raw':'ordinary';this.data=this.dataByMode[this.mode];
    this.maximum=$('color-scale').value==='fixed'?255:Math.max(8,...Object.values(this.dataByMode).flatMap(data=>Object.values(data).flatMap(e=>e.values||[])));
    const rawLabel=statusNames[raw.status]||'等待原始数据';
    $('raw-status').className='raw-status'+(raw.status==='ready'?' ready':'');
    $('raw-status').textContent=raw.status==='ready'?`${rawTotal} 点完整返回 · 五指 / 手心 / 手背同时显示`:raw.error||rawLabel;
    $('ordinary-total').textContent=ordinaryTotal?`${ordinaryTotal} 点 · 独立读取`:'等待普通触觉';
    $('tactile-description').textContent='点位按官方上位机排布 · 0 号从指根端开始';
    $('legend-high').textContent=this.maximum;
    document.querySelector('.tactile-panel').classList.toggle('hide-values',!$('show-point-values').checked);
    document.querySelector('.tactile-panel').classList.toggle('hide-indices',!$('show-point-indices').checked);
    for(const [mode,data]of Object.entries(this.dataByMode))for(const region of REGIONS){
      const record=this.cards.get(mode+':'+region),{card}=record,entry=data[region],values=entry?.values,svg=card.querySelector('svg');
      const available=Array.isArray(values)&&values.length>0,expected=(mode==='raw'?RAW_COUNTS:ORDINARY_COUNTS)[region];
      const fresh=state.connected&&available&&Date.now()-Date.parse(entry.sampled_at)<3000;
      card.classList.toggle('stale',!fresh);card.querySelector('.card-count').textContent=available?`${values.length} 点`:`-- / ${expected} 点`;
      card.querySelector('.card-empty').hidden=available;svg.toggleAttribute('hidden',!available);
      card.querySelector('.card-empty').textContent=mode==='raw'?rawLabel:entry?.error||'等待数据';
      const stats=tactileStatistics(values);card.querySelector('.card-peak').textContent=stats?`峰值 ${stats.maximum} · 非零 ${stats.active}`:'未返回';
      card.querySelector('.card-age').textContent=fresh?'实时':available?'已过期':'';
      if(!available){svg.replaceChildren();record.signature=null;record.layout=null;continue;}
      const layout=tactileLayout(region,values.length,{side:state.device?.hand||'right'});record.layout=layout;
      const signature=[values.length,state.device?.hand,layout.kind].join(':');
      card.querySelector('.taxel-orientation').textContent=layout.finger?'指尖 ↑ · 指根 0 / 1':layout.kind==='toolbox-relative'?'上位机原始轮廓':'普通降采样索引';
      if(record.signature!==signature){
        record.signature=signature;svg.replaceChildren();svg.setAttribute('viewBox',`0 0 ${layout.width} ${layout.height}`);
        svg.dataset.layout=layout.kind;svg.dataset.pointCount=values.length;
        for(const p of layout.points){
          const group=svgNode('g',{'data-index':p.index,'data-region':region,'data-source':mode,role:'button',tabindex:0,class:'tactile-point'});
          group.append(svgNode('rect',{x:p.x,y:p.y,width:p.width,height:p.height,rx:5}));
          const index=svgNode('text',{x:p.x+4,y:p.y+8,class:'point-index'});index.textContent=p.index;
          const value=svgNode('text',{x:p.x+p.width/2,y:p.y+p.height*.76,class:'point-value','text-anchor':'middle'});
          group.append(index,value);svg.append(group);
        }
      }
      for(const group of svg.children){
        const index=Number(group.dataset.index),value=values[index];
        group.querySelector('rect').setAttribute('fill',tactileColor(value,this.maximum));
        group.querySelector('.point-value').textContent=value;group.classList.toggle('high-reading',value/this.maximum>.6);
        group.setAttribute('aria-label',`${mode==='raw'?'原始':'普通'}${REGION_NAMES[region]} 索引 ${index}，触觉值 ${value}${fresh?'':'，已过期'}`);
      }
    }
    if(!this.dataByMode[this.pointMode]?.[this.region]?.values?.length)this.pointMode=this.mode;
    this.inspect();
    return {data:this.data,total:this.mode==='raw'?rawTotal:ordinaryTotal,mode:this.mode,maximum:this.maximum,rawLabel,rawTotal,ordinaryTotal};
  }
}
