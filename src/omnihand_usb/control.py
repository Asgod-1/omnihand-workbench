"""Explicit, queued O10 position control. Nothing moves on connect or preview.

All device calls below run on Monitor's sampling thread. HTTP only submits
requests; the adapter/SDK is never called by a web handler or preview worker.
"""
from __future__ import annotations
import copy
from dataclasses import dataclass,field
from datetime import datetime
import json
import math
from pathlib import Path
import queue
import secrets
import threading
import time

from .motion_protocol import target_vector
from .control_profile import control_profile,joint_target
from .gesture_transition import gesture_stages

MAX_RATE=800.0             # optional host interpolation rate, encoder counts/s
MAX_STEP=80                # maximum per-axis change per submitted waypoint
TICK_SECONDS=.10
POSITION_TOLERANCE=40
CANFD_FINAL_TOLERANCE=64
POSITION_STALL_SECONDS=1.15
POSITION_STALL_DELTA=12


def timestamp():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def duration_value(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not .5<=value<=30:
        raise ValueError('渐变时长必须在0.5–30秒之间')
    return float(value)


def execution_mode_value(value):
    if value not in ('direct','smooth','staged'):raise ValueError('发送方式必须为direct、smooth或staged')
    return value


def nominal(values):
    if len(values)!=10 or any(type(v) is not int for v in values):raise ValueError('没有有效的十轴位置反馈')
    # This is a new command target; original telemetry is never modified.
    return [max(0,min(4096,v)) for v in values]


@dataclass
class MotionPlan:
    start:list
    target:list
    duration:float
    began:float
    name:str
    execution_mode:str='smooth'
    profile:dict=field(default_factory=lambda:{'control_mode':'position'})
    id:str=field(default_factory=lambda:secrets.token_hex(6))
    last_sent:list=field(default_factory=list)
    last_tick:float=-math.inf
    phase:str='moving'
    settle_at:float|None=None
    reached_samples:int=0
    checked_feedback_at:float=0.0
    stationary_pose:list|None=None
    stationary_since:float=0.0
    stages:list[tuple[str,list[int]]]=field(default_factory=list)
    stage_index:int=0

    def __post_init__(self):
        self.start=target_vector(self.start);self.target=target_vector(self.target)
        execution_mode_value(self.execution_mode)
        self.duration=0.0 if self.execution_mode!='smooth' else max(duration_value(self.duration),max(abs(a-b) for a,b in zip(self.start,self.target))*1.5/MAX_RATE)
        self.last_sent=list(self.start)

    @property
    def arrival_timeout(self):
        return 3.0 if self.execution_mode=='smooth' else 10.0

    @property
    def stage(self):
        if not self.stages:
            return None
        name, _ = self.stages[self.stage_index]
        return {"index": self.stage_index + 1, "total": len(self.stages), "name": name}

    def waypoint(self,now):
        if self.execution_mode!='smooth':return list(self.target)
        t=min(1,max(0,(now-self.began)/self.duration));smooth=t*t*(3-2*t)
        desired=[round(a+(b-a)*smooth) for a,b in zip(self.start,self.target)]
        return [last+max(-MAX_STEP,min(MAX_STEP,want-last)) for want,last in zip(desired,self.last_sent)]


@dataclass
class Ticket:
    action:str
    body:dict
    done:threading.Event=field(default_factory=threading.Event)
    result:object=None
    error:Exception|None=None
    cancelled:bool=False
    started:bool=False
    generation:int=0


class MotionController:
    def __init__(self,root):
        self.root=Path(root);self._lock=threading.RLock();self._preview_lock=threading.Lock()
        self._queue=queue.PriorityQueue();self._sequence=0;self._generation=0;self._cancel=threading.Event()
        self._device=None;self._plan=None;self._owner=None;self._log_path=None;self._feedback=None;self._feedback_at=0.0
        self._solvers={}
        self._builtin=json.loads((Path(__file__).parent/'presets.json').read_text(encoding='utf-8'))['presets']
        self._state={};self.unbind()

    def _reset(self,device=None,mode=None,side='right'):
        self._device=device;self._plan=None;self._owner=None;self._feedback=None;self._feedback_at=0
        self._diagnostics_at=-math.inf;self._power_at=-math.inf;self._last_jog_at=-math.inf
        self._cancel.clear();self._log_path=None
        self._state={'available':device is not None and 'position' in getattr(device,'control_capabilities',()),
            'capabilities':list(getattr(device,'control_capabilities',())),'armed':False,
            'session_id':secrets.token_hex(16),'mode':mode,'hand':side,'running':False,'jog_active':False,'control_mode':'position','status':'locked' if device else 'disconnected',
            'power_state':None,'motion_id':None,'name':None,'target':None,'last_sent':None,'write_attempted':False,
            'progress':0.0,'effective_duration':None,'execution_mode':None,'arrival_timeout':None,'stage':None,
            'position_error':None,'arrival_tolerance':None,'error':None,'log_path':None,
            'message':'控制已锁定；连接、预览及调整滑块不会下发动作' if device else '请先连接设备'}

    def bind(self,device,mode,side):
        with self._lock:self._reset(device,mode,side)

    def unbind(self):
        with self._lock:
            while True:
                try:_,_,ticket=self._queue.get_nowait()
                except queue.Empty:break
                ticket.error=RuntimeError('连接已结束，排队指令已取消');ticket.done.set()
            self._reset()

    def snapshot(self):
        with self._lock:return copy.deepcopy(self._state)

    def _update(self,**changes):
        with self._lock:self._state.update(changes)

    def feedback(self):
        """Called by the owner thread after tick; timestamps are monotonic."""
        return (list(self._feedback),self._feedback_at) if self._feedback is not None else (None,0)

    def needs_fast_feedback(self):
        return self._state['running'] or (self._state['jog_active'] and time.monotonic()-self._last_jog_at<1.5)

    def _custom(self):
        path=self.root/'data/actions.local.json'
        if not path.exists():return []
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or not isinstance(data.get('presets'),list):raise ValueError('自定义动作文件格式错误，未覆盖原文件')
        for p in data['presets']:
            target_vector(p['positions'])
            if p.get('hand') not in ('right','left') or not isinstance(p.get('name'),str):raise ValueError('自定义动作内容不合法')
        return data['presets']

    def catalog(self):
        control = self.snapshot()
        side = control['hand']
        with self._lock:
            presets=[copy.deepcopy(p) for p in self._builtin+self._custom() if p['hand']==side]
        for preset in presets:
            demo_positions = preset.pop('demo_positions', None)
            if control['mode'] == 'demo' and demo_positions is not None:
                preset['positions'] = target_vector(demo_positions)
                preset['demo_adjusted'] = True
        return {'presets':presets,'hand':side,'position_range':[0,4096],
                'execution_modes':['direct','smooth','staged'] if side=='right' else ['direct','smooth'],'default_execution_mode':'direct',
                'control_modes':[m for m in ('position','pt','pvt') if m=='position' or m in control['capabilities']],
                'command_limits':{'applies_to':'smooth','max_step':MAX_STEP,'max_rate':MAX_RATE,'tick_seconds':TICK_SECONDS},'control':control}

    def _resolve(self,body):
        if ('preset_id' in body)==('positions' in body):raise ValueError('请指定一个预设动作或十轴目标，不能同时指定')
        if 'positions' in body:return target_vector(body['positions']),'自定义目标'
        for preset in self.catalog()['presets']:
            if preset['id']==body['preset_id']:return list(preset['positions']),preset['name']
        raise ValueError('没有找到适用于当前手的预设动作')

    def preview(self,body):
        positions,name=self._resolve(body);side=self.snapshot()['hand']
        pose=None;error=None
        try:
            with self._preview_lock:
                if side not in self._solvers:
                    from .kinematics import PoseSolver
                    self._solvers[side]=PoseSolver(side)
                pose=self._solvers[side].solve(positions)
        except (ImportError,OSError,ValueError,RuntimeError) as exc:error=str(exc)
        return {'positions':positions,'name':name,'hand':side,'pose':pose,'pose_error':error,'hardware_write':False}

    def save(self,body):
        name=body.get('name')
        if not isinstance(name,str) or not name.strip() or len(name.strip())>32 or any(ord(c)<32 for c in name):
            raise ValueError('动作名称需要1–32个可见字符')
        positions=target_vector(body.get('positions'));name=name.strip();side=self.snapshot()['hand']
        with self._lock:
            presets=self._custom()
            if any(p['name']==name and p['hand']==side for p in self._builtin+presets):raise ValueError('动作名称已存在，请换一个名称')
            preset={'id':'user_'+secrets.token_hex(6),'name':name,'icon':'◇','hand':side,'positions':positions,'source':'user','description':'本机自定义位置动作'}
            presets.append(preset);folder=self.root/'data';folder.mkdir(parents=True,exist_ok=True)
            path=folder/'actions.local.json';temporary=folder/'actions.local.json.tmp'
            temporary.write_text(json.dumps({'schema':1,'presets':presets},ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
        return {'preset':preset,'hardware_write':False}

    def request(self,action,body,timeout=8):
        if action=='preview':return self.preview(body)
        if action=='save':return self.save(body)
        if action not in ('arm','lock','enable','disable','move','stop','joint'):raise ValueError('不支持的控制请求')
        if action in ('move','joint'):profile=control_profile(body)
        if action=='joint':joint_target(body.get('index'),body.get('position'))
        if action=='move':
            self._resolve(body)
            mode=execution_mode_value(body.get('execution_mode','direct'))
            if mode!='direct' and profile['control_mode']!='position':
                raise ValueError('混合控制使用直接发送，渐变和分段切换用于位置控制')
            if mode=='smooth':
                duration_value(body.get('duration',4))
        with self._lock:
            if body.get('session_id')!=self._state['session_id']:raise RuntimeError('设备会话已变化，请刷新后重试')
            if self._device is None or not self._state['available']:raise RuntimeError('当前连接不支持位置控制')
            if action in ('lock','disable','stop'):
                self._generation+=1;self._cancel.set()
            elif self._cancel.is_set():raise RuntimeError('停止请求正在处理，请等待确认')
            self._sequence+=1;ticket=Ticket(action,dict(body),generation=self._generation)
            self._queue.put((0 if action in ('lock','disable','stop') else 1,self._sequence,ticket))
        if not ticket.done.wait(timeout):
            with self._lock:
                ticket.cancelled=True;started=ticket.started
                if started and action in ('arm','enable','move','joint'):
                    # A timed-out in-flight request must not start/continue a later plan.
                    self._generation+=1;self._cancel.set();self._sequence+=1
                    cancellation=Ticket('lock',{'session_id':self._state['session_id']},generation=self._generation)
                    self._queue.put((0,self._sequence,cancellation))
            raise TimeoutError('控制请求超时，执行状态未确认' if started else '控制队列超时；该排队请求已取消')
        if ticket.error:raise ticket.error
        return ticket.result

    def _record(self,event,**details):
        try:
            if self._log_path is None:
                folder=self.root/'data';folder.mkdir(parents=True,exist_ok=True)
                self._log_path=folder/('motion_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.jsonl')
                self._update(log_path='data/'+self._log_path.name)
            with self._log_path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'at':timestamp(),'event':event,'mode':self._state['mode'],**details},ensure_ascii=False)+'\n')
        except OSError as exc:self._update(log_error=str(exc))

    def _read_positions(self,device):
        positions=device.positions()
        nominal(positions)
        self._feedback=list(positions);self._feedback_at=time.monotonic()
        return positions

    @staticmethod
    def _diagnostic_fault(diagnostics):
        return diagnostics.get('hand_error_code',0) or any(any(r.values()) for r in diagnostics.get('motor_errors',[]))

    def _healthy(self,device,*,reuse=False,fresh_positions=False):
        if reuse and not fresh_positions and self._feedback is not None and time.monotonic()-self._feedback_at<=.55:
            positions=self._feedback
        else:
            positions=self._read_positions(device)
        if not reuse or time.monotonic()-self._diagnostics_at>3.3:
            self._diagnostics_at=-math.inf
            diagnostics,errors=device.diagnostics()
            if errors:raise RuntimeError('诊断未完整返回：'+'；'.join(errors))
            if self._diagnostic_fault(diagnostics):
                raise RuntimeError('设备存在错误标记，请检查设备状态')
            self._diagnostics_at=time.monotonic()
        power=self._state['power_state']
        if 'power' in self._state['capabilities'] and (not reuse or time.monotonic()-self._power_at>1):
            self._power_at=-math.inf
            power=device.get_power_state()
            self._power_at=time.monotonic()
            self._update(power_state=power)
        if power==2:raise RuntimeError('设备处于校准状态，未允许位置控制')
        return positions

    def _write(self,device,positions,profile=None,index=None):
        positions=target_vector(positions);profile=profile or {'control_mode':'position'}
        self._update(write_attempted=True)
        ack_only=index is not None and profile['control_mode']=='position' and hasattr(device,'submit_joint_position')
        if profile['control_mode']!='position':result=device.set_mixed(positions,profile,index)
        elif ack_only:result=device.submit_joint_position(index,positions[index-1])
        elif index is not None:result=device.set_joint_position(index,positions[index-1])
        else:result=device.set_positions(positions)
        if not isinstance(result,dict):raise RuntimeError('设备未返回有效的位置指令应答')
        actual=result.get('positions')
        if ack_only:
            if (result.get('ack_joint_index')!=index or type(result.get('ack_joint_position')) is not int
                    or type(result.get('hand_error_code')) is not int or result.get('fault_check')!='private_0x0d'):
                raise RuntimeError('设备未确认对应关节写入及故障状态')
        elif actual is None or len(actual)!=10:
            raise RuntimeError('设备未返回完整的位置指令应答')
        event='mixed_target_submitted' if profile['control_mode']!='position' else 'joint_position_acknowledged' if index is not None else 'position_acknowledged'
        self._record(event,target=list(positions),joint_index=index,profile=profile,positions=actual,ack_joint_position=result.get('ack_joint_position'),faults=result.get('faults'),
            ack_status_raw=result.get('ack_status_raw'),hand_error_code=result.get('hand_error_code'),
            fault_check=result.get('fault_check'),response_hex=result.get('response_hex'),delivery=result.get('delivery'),request_hex=result.get('request_hex'))
        if result.get('hand_error_code',0):
            self._diagnostics_at=-math.inf
            raise RuntimeError(f"位置指令后设备报告故障 0x{result['hand_error_code']:04X}，动作已停止下发")
        if any(result.get('faults') or []):raise RuntimeError('位置应答中存在轴故障，动作已停止下发')
        if result.get('fault_check')=='private_0x0d':self._diagnostics_at=time.monotonic()
        if actual is not None:
            self._feedback=list(actual);self._feedback_at=time.monotonic()
        return result

    def _hold(self,device):
        current=device.positions();result=self._write(device,nominal(current))
        self._update(last_sent=nominal(current))
        return result

    def _armed(self):
        if not self._state['armed']:raise RuntimeError('控制已锁定，请先开启控制')

    def _execute(self,ticket,device):
        action,body=ticket.action,ticket.body
        if body.get('session_id')!=self._state['session_id']:raise RuntimeError('旧设备会话的请求已取消')
        if action in ('arm','enable','move','joint') and (ticket.generation!=self._generation or self._cancel.is_set()):
            raise RuntimeError('该请求已被停止或锁定操作取消')
        if action=='arm':
            if self._state['running']:raise RuntimeError('动作正在执行')
            self._healthy(device)
            if ticket.generation!=self._generation:raise RuntimeError('开启控制已被取消')
            self._update(armed=True,status='ready',error=None,message='控制已开启；拖动滑块直接控制单轴，预设需点击发送')
            self._record('control_armed',hardware_write=False)
        elif action=='enable':
            self._armed()
            if self._state['running']:raise RuntimeError('请先停止当前动作')
            if 'power' not in self._state['capabilities']:raise RuntimeError('当前SDK接口未提供电机使能，请使用已使能的设备')
            current=self._healthy(device)
            if self._cancel.is_set():raise RuntimeError('使能已被取消')
            if self._state['power_state']==1:
                self._update(status='ready',error=None,message='电机已经使能；未重复下发使能或位置指令')
                return self.snapshot()
            self._write(device,nominal(current))  # preload current pose before enabling
            if self._cancel.is_set():raise RuntimeError('使能已被取消')
            result=device.set_enabled(True)
            self._power_at=time.monotonic()
            self._update(power_state=result['power_state'],status='ready',error=None,message='电机已使能，状态回读确认')
            self._record('power_enabled',confirmed=True)
        elif action in ('stop','lock','disable'):
            was_running=self._state['running'] or self._state['jog_active'];self._plan=None;self._update(running=False,jog_active=False)
            try:
                if action=='disable':
                    if 'power' not in self._state['capabilities']:raise RuntimeError('当前SDK接口未提供电机失能')
                    result=device.set_enabled(False);self._update(power_state=result['power_state'],armed=False)
                    self._power_at=time.monotonic()
                    self._update(status='disabled',message='电机已失能，已释放保持力')
                    self._record('power_disabled',confirmed=True)
                else:
                    held=(self._state['armed'] or self._state['write_attempted']) if action=='stop' else self._state['armed'] and was_running
                    if held:self._hold(device)
                    if action=='lock':self._update(armed=False)
                    self._update(status='locked' if action=='lock' else 'stopped',message='控制已锁定' if action=='lock' else '停止下发并已确认保持指令' if held else '已停止下发；未发送保持指令')
                    self._record(action,hold_acknowledged=held)
                self._update(error=None)
            finally:self._cancel.clear()
        elif action in ('move','joint'):
            self._armed()
            if self._state['running']:raise RuntimeError('已有动作在执行，请先停止')
            profile=control_profile(body)
            if profile['control_mode']!='position' and profile['control_mode'] not in self._state['capabilities']:
                raise RuntimeError('当前连接不支持所选混合控制')
            if action=='joint' and profile['control_mode']!='position' and 'mixed_joint' not in self._state['capabilities']:
                raise RuntimeError('当前连接只支持整手混合控制，单轴请用位置模式或CAN FD')
            mode = execution_mode_value(body.get('execution_mode','direct')) if action=='move' else 'direct'
            if mode=='staged' and self._state['hand']!='right':
                raise ValueError('分段切换当前只支持O10右手')
            current=self._healthy(device,reuse=self._state['mode'] in ('canfd','demo'))
            if self._state['power_state']==0:raise RuntimeError('电机尚未使能，请先使能')
            if self._cancel.is_set():raise RuntimeError('动作已被停止请求取消')
            if action=='joint':
                index,position=joint_target(body.get('index'),body.get('position'))
                target=nominal(current);target[index-1]=position
                self._write(device,target,profile,index)
                self._last_jog_at=time.monotonic()
                self._update(status='joint_sent',jog_active=True,motion_id=None,name=f'关节{index}',target=target,last_sent=target,
                    progress=0,execution_mode='direct',stage=None,position_error=None,arrival_tolerance=None,
                    control_mode=profile['control_mode'],error=None,message=f'关节{index}目标已发送')
                return self.snapshot()
            target,name=self._resolve(body)
            start=nominal(current)
            stages=[]
            if mode=='staged':
                open_pose=next(p['positions'] for p in self._builtin if p['id']=='open' and p['hand']=='right')
                stages=gesture_stages(start,target,open_pose,POSITION_TOLERANCE)
            plan=MotionPlan(start,target,body.get('duration',4),time.monotonic(),name,execution_mode=mode,profile=profile,stages=stages)
            self._plan=plan
            self._update(running=True,jog_active=False,status='moving',motion_id=plan.id,name=name,target=target,last_sent=list(plan.start),progress=0,control_mode=profile['control_mode'],
                effective_duration=round(plan.duration,2),execution_mode=mode,arrival_timeout=plan.arrival_timeout,stage=plan.stage,
                position_error=None,arrival_tolerance=None,
                error=None,message={'direct':'正在直接发送位置目标','smooth':'正在渐变执行位置目标','staged':'开始分段切换'}[mode])
            self._record('motion_started',motion_id=plan.id,name=name,target=target,effective_duration=plan.duration,
                execution_mode=mode,profile=profile,arrival_timeout=plan.arrival_timeout,stages=stages)
        return self.snapshot()

    def tick(self,device,feedback=None,*,poll_missing_feedback=True):
        if self._owner is None:self._owner=threading.get_ident()
        if self._owner!=threading.get_ident():raise RuntimeError('动作队列必须由设备采集线程执行')
        if feedback is not None:
            nominal(feedback)
            self._feedback=list(feedback);self._feedback_at=time.monotonic()
        for _ in range(16):
            try:_,_,ticket=self._queue.get_nowait()
            except queue.Empty:break
            with self._lock:
                if ticket.cancelled:ticket.done.set();continue
                ticket.started=True
            try:ticket.result=self._execute(ticket,device)
            except Exception as exc:
                ticket.error=exc
                if ticket.action in ('stop','disable','lock','enable','joint'):
                    self._plan=None;self._update(armed=False,running=False,jog_active=False,status='error',error=str(exc),message='控制请求未确认，请检查设备')
                self._record('request_failed',action=ticket.action,error=str(exc))
            finally:ticket.done.set()
        if self._plan is None or self._cancel.is_set():return
        try:self._advance(device,poll_missing_feedback=poll_missing_feedback)
        except Exception as exc:
            self._plan=None;self._update(armed=False,running=False,status='error',error=str(exc),message='动作已停止下发；硬件状态请以回读为准')
            self._record('motion_failed',error=str(exc))

    def _begin_settling(self,plan,message):
        plan.phase='settling'
        plan.settle_at=time.monotonic()
        plan.checked_feedback_at=self._feedback_at
        plan.stationary_pose=None
        final_stage=plan.stage_index+1>=len(plan.stages)
        tolerance=POSITION_TOLERANCE
        if final_stage and self._state['mode']=='demo':
            tolerance=0
        elif final_stage and self._state['mode']=='canfd' and plan.profile['control_mode']=='position':
            tolerance=CANFD_FINAL_TOLERANCE
        self._update(status='settling',message=message,position_error=None,arrival_tolerance=tolerance)

    def _end_unreached(self,device,plan,distance,reason):
        self._hold(device)
        name=plan.stage['name'] if plan.stage else plan.name
        detail=f'，最大位置偏差 {distance}' if distance is not None else ''
        self._update(running=False,status='not_reached',message='已确认保持当前位置',
                     error=f'{name}{reason}{detail}，已发送保持')
        self._plan=None
        self._record('motion_not_reached',motion_id=plan.id,max_position_error=distance,stage=plan.stage,reason=reason)

    def _advance(self,device,*,poll_missing_feedback):
        plan=self._plan;now=time.monotonic()
        if self._cancel.is_set():return
        stage_target = plan.stages[plan.stage_index][1] if plan.stages else plan.target
        if plan.phase=='settling':
            if self._feedback_at<=plan.checked_feedback_at:
                if not poll_missing_feedback:
                    if now-plan.settle_at>plan.arrival_timeout:
                        self._end_unreached(device,plan,None,'等待位置反馈超时')
                    return
                if now-plan.last_tick<.06:return
                self._read_positions(device)
            plan.last_tick=now
            plan.checked_feedback_at=self._feedback_at
            distance=max(abs(a-b) for a,b in zip(self._feedback,stage_target))
            tolerance=self._state['arrival_tolerance']
            self._update(position_error=distance)
            plan.reached_samples=plan.reached_samples+1 if distance<=tolerance else 0
            if plan.reached_samples>=2:
                if plan.stage_index + 1 < len(plan.stages):
                    self._record('transition_stage_reached', motion_id=plan.id, stage=plan.stage, max_position_error=distance)
                    plan.stage_index += 1
                    plan.phase = 'moving'
                    plan.reached_samples = 0
                    self._update(status='moving', stage=plan.stage, progress=plan.stage_index / len(plan.stages),
                                 position_error=None,arrival_tolerance=None,message=f"准备{plan.stage['name']}")
                    return
                self._update(running=False,status='completed',progress=1.0,message='动作完成，反馈已到位');self._plan=None
                self._record('motion_completed',motion_id=plan.id,max_position_error=distance)
                return
            if plan.stationary_pose is None or max(abs(a-b) for a,b in zip(self._feedback,plan.stationary_pose))>POSITION_STALL_DELTA:
                plan.stationary_pose=self._feedback
                plan.stationary_since=self._feedback_at
            if now-plan.settle_at>plan.arrival_timeout:
                self._end_unreached(device,plan,distance,'未到位')
            elif (plan.profile['control_mode']=='position' and not plan.reached_samples
                  and self._feedback_at-plan.stationary_since>=POSITION_STALL_SECONDS):
                self._end_unreached(device,plan,distance,'已停滞且未到位')
            return
        if plan.execution_mode!='smooth':
            if plan.stage_index:
                # A leg may only start while the previous clearance pose is still present.
                current = self._healthy(device,reuse=self._state['mode'] in ('canfd','demo'),fresh_positions=True)
                if self._state['power_state']==0:
                    raise RuntimeError('分段切换过程中电机已失能')
                previous_target = plan.stages[plan.stage_index - 1][1]
                if max(abs(a-b) for a,b in zip(current,previous_target)) > POSITION_TOLERANCE:
                    raise RuntimeError('上一段姿态已偏离到位范围，未继续发送')
                if self._cancel.is_set():
                    return
            self._write(device,stage_target,plan.profile)
            plan.last_sent=list(stage_target)
            message = '目标已直接下发，等待实际位置到位'
            if plan.stages:
                message = f"{plan.stage['name']}已下发，等待位置到位"
                self._record('transition_stage_sent', motion_id=plan.id, stage=plan.stage, target=stage_target)
            self._update(last_sent=list(stage_target))
            self._begin_settling(plan,message)
            return
        if now-plan.last_tick<TICK_SECONDS:return
        plan.last_tick=now
        target=plan.waypoint(now)
        if target!=plan.last_sent:
            self._write(device,target);plan.last_sent=target;self._update(last_sent=list(target))
        self._update(progress=min(.99,max(0,(now-plan.began)/plan.duration)))
        if now-plan.began>=plan.duration and plan.last_sent==plan.target:
            self._begin_settling(plan,'目标已下发，等待实际位置到位')

    def check_diagnostics(self,diagnostics,errors=()):
        bad=self._diagnostic_fault(diagnostics)
        self._diagnostics_at=time.monotonic() if diagnostics and not (bad or errors) else -math.inf
        if not (self._state.get('running') or self._state.get('jog_active')):return
        if bad or errors:
            reason='设备诊断出现错误标记' if bad else '运行中诊断未完整返回：'+'；'.join(errors)
            self._plan=None;self._update(armed=False,running=False,jog_active=False,status='error',error=reason+'，已停止后续动作下发',message='设备异常，动作已停止下发')
            self._record('motion_fault',diagnostics=diagnostics,errors=list(errors))
