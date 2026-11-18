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

MAX_RATE=800.0             # optional host interpolation rate, encoder counts/s
MAX_STEP=80                # maximum per-axis change per submitted waypoint
TICK_SECONDS=.10
POSITION_TOLERANCE=40


def timestamp():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def duration_value(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not .5<=value<=30:
        raise ValueError('渐变时长必须在0.5–30秒之间')
    return float(value)


def execution_mode_value(value):
    if value not in ('direct','smooth'):raise ValueError('发送方式必须为direct或smooth')
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
    id:str=field(default_factory=lambda:secrets.token_hex(6))
    last_sent:list=field(default_factory=list)
    last_tick:float=-math.inf
    phase:str='moving'
    settle_at:float|None=None
    reached_samples:int=0
    checked_feedback_at:float=0.0

    def __post_init__(self):
        self.start=target_vector(self.start);self.target=target_vector(self.target)
        execution_mode_value(self.execution_mode)
        self.duration=0.0 if self.execution_mode=='direct' else max(duration_value(self.duration),max(abs(a-b) for a,b in zip(self.start,self.target))*1.5/MAX_RATE)
        self.last_sent=list(self.start)

    @property
    def arrival_timeout(self):
        return 10.0 if self.execution_mode=='direct' else 3.0

    def waypoint(self,now):
        if self.execution_mode=='direct':return list(self.target)
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
        self._cancel.clear();self._log_path=None
        self._state={'available':device is not None and 'position' in getattr(device,'control_capabilities',()),
            'capabilities':list(getattr(device,'control_capabilities',())),'armed':False,
            'session_id':secrets.token_hex(16),'mode':mode,'hand':side,'running':False,'status':'locked' if device else 'disconnected',
            'power_state':None,'motion_id':None,'name':None,'target':None,'last_sent':None,'write_attempted':False,
            'progress':0.0,'effective_duration':None,'execution_mode':None,'arrival_timeout':None,'error':None,'log_path':None,
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
        side=self.snapshot()['hand']
        with self._lock:
            presets=[copy.deepcopy(p) for p in self._builtin+self._custom() if p['hand']==side]
        return {'presets':presets,'hand':side,'position_range':[0,4096],
                'execution_modes':['direct','smooth'],'default_execution_mode':'direct',
                'command_limits':{'applies_to':'smooth','max_step':MAX_STEP,'max_rate':MAX_RATE,'tick_seconds':TICK_SECONDS},'control':self.snapshot()}

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
        if action not in ('arm','lock','enable','disable','move','stop'):raise ValueError('不支持的控制请求')
        if action=='move':
            self._resolve(body)
            if execution_mode_value(body.get('execution_mode','direct'))=='smooth':duration_value(body.get('duration',4))
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
                if started and action in ('arm','enable','move'):
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

    def _healthy(self,device):
        positions=device.positions();nominal(positions)
        diagnostics,errors=device.diagnostics()
        if errors:raise RuntimeError('诊断未完整返回：'+'；'.join(errors))
        if diagnostics.get('hand_error_code',0) or any(any(v for v in error.values()) for error in diagnostics.get('motor_errors',[])):
            raise RuntimeError('设备存在错误标记，请检查设备状态')
        power=device.get_power_state() if 'power' in self._state['capabilities'] else None
        if power==2:raise RuntimeError('设备处于校准状态，未允许位置控制')
        self._update(power_state=power);self._feedback=list(positions);self._feedback_at=time.monotonic()
        return positions

    def _write(self,device,positions):
        self._update(write_attempted=True)
        result=device.set_positions(target_vector(positions))
        if not isinstance(result,dict) or len(result.get('positions',[]))!=10:raise RuntimeError('设备未返回完整的位置指令应答')
        self._record('position_acknowledged',target=list(positions),positions=result['positions'],faults=result.get('faults'),
            ack_status_raw=result.get('ack_status_raw'),hand_error_code=result.get('hand_error_code'),
            fault_check=result.get('fault_check'),response_hex=result.get('response_hex'))
        if result.get('hand_error_code',0):raise RuntimeError(f"位置指令后设备报告故障 0x{result['hand_error_code']:04X}，动作已停止下发")
        if any(result.get('faults') or []):raise RuntimeError('位置应答中存在轴故障，动作已停止下发')
        self._feedback=list(result['positions']);self._feedback_at=time.monotonic()
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
        if action in ('arm','enable','move') and (ticket.generation!=self._generation or self._cancel.is_set()):
            raise RuntimeError('该请求已被停止或锁定操作取消')
        if action=='arm':
            if self._state['running']:raise RuntimeError('动作正在执行')
            self._healthy(device)
            if ticket.generation!=self._generation:raise RuntimeError('开启控制已被取消')
            self._update(armed=True,status='ready',error=None,message='控制已开启；执行按钮才会下发动作')
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
            self._update(power_state=result['power_state'],status='ready',error=None,message='电机已使能，状态回读确认')
            self._record('power_enabled',confirmed=True)
        elif action in ('stop','lock','disable'):
            was_running=self._state['running'];self._plan=None;self._update(running=False)
            try:
                if action=='disable':
                    if 'power' not in self._state['capabilities']:raise RuntimeError('当前SDK接口未提供电机失能')
                    result=device.set_enabled(False);self._update(power_state=result['power_state'],armed=False)
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
        elif action=='move':
            self._armed()
            if self._state['running']:raise RuntimeError('已有动作在执行，请先停止')
            current=self._healthy(device)
            if self._state['power_state']==0:raise RuntimeError('电机尚未使能，请先使能')
            if self._cancel.is_set():raise RuntimeError('动作已被停止请求取消')
            target,name=self._resolve(body)
            mode=execution_mode_value(body.get('execution_mode','direct'))
            plan=MotionPlan(nominal(current),target,body.get('duration',4),time.monotonic(),name,execution_mode=mode)
            self._plan=plan
            self._update(running=True,status='moving',motion_id=plan.id,name=name,target=target,last_sent=list(plan.start),progress=0,
                effective_duration=round(plan.duration,2),execution_mode=mode,arrival_timeout=plan.arrival_timeout,
                error=None,message='正在直接发送位置目标' if mode=='direct' else '正在渐变执行位置目标')
            self._record('motion_started',motion_id=plan.id,name=name,target=target,effective_duration=plan.duration,
                execution_mode=mode,arrival_timeout=plan.arrival_timeout)
        return self.snapshot()

    def tick(self,device,feedback=None):
        if self._owner is None:self._owner=threading.get_ident()
        if self._owner!=threading.get_ident():raise RuntimeError('动作队列必须由设备采集线程执行')
        if feedback is not None:self._feedback=list(feedback);self._feedback_at=time.monotonic()
        for _ in range(16):
            try:_,_,ticket=self._queue.get_nowait()
            except queue.Empty:break
            with self._lock:
                if ticket.cancelled:ticket.done.set();continue
                ticket.started=True
            try:ticket.result=self._execute(ticket,device)
            except Exception as exc:
                ticket.error=exc
                if ticket.action in ('stop','disable','lock','enable'):
                    self._plan=None;self._update(armed=False,running=False,status='error',error=str(exc),message='控制请求未确认，请检查设备')
                self._record('request_failed',action=ticket.action,error=str(exc))
            finally:ticket.done.set()
        if self._plan is None or self._cancel.is_set():return
        try:self._advance(device)
        except Exception as exc:
            self._plan=None;self._update(armed=False,running=False,status='error',error=str(exc),message='动作已停止下发；硬件状态请以回读为准')
            self._record('motion_failed',error=str(exc))

    def _advance(self,device):
        plan=self._plan;now=time.monotonic()
        if now-plan.last_tick<TICK_SECONDS:return
        plan.last_tick=now
        if self._feedback is None or now-self._feedback_at>.2 or (plan.phase=='settling' and self._feedback_at<=plan.checked_feedback_at):
            self._feedback=list(device.positions());self._feedback_at=time.monotonic()
        if self._cancel.is_set():return
        if plan.phase=='settling':
            plan.checked_feedback_at=self._feedback_at
            distance=max(abs(a-b) for a,b in zip(self._feedback,plan.target))
            plan.reached_samples=plan.reached_samples+1 if distance<=POSITION_TOLERANCE else 0
            if plan.reached_samples>=2:
                self._update(running=False,status='completed',progress=1.0,message='动作完成，位置反馈已到目标范围');self._plan=None
                self._record('motion_completed',motion_id=plan.id,max_position_error=distance)
            elif now-plan.settle_at>plan.arrival_timeout:
                self._hold(device);self._update(running=False,status='not_reached',message='未在等待时间内到位，已发送保持当前位置',error='目标未到位，可能接触阻挡或需要检查状态');self._plan=None
                self._record('motion_not_reached',motion_id=plan.id,max_position_error=distance)
            return
        if plan.execution_mode=='direct':
            # Send the full target once. Later ticks read feedback, never replay it.
            self._write(device,plan.target);plan.last_sent=list(plan.target)
            plan.phase='settling';plan.settle_at=time.monotonic()
            self._update(last_sent=list(plan.target),status='settling',message='目标已直接下发，等待实际位置到位')
            return
        target=plan.waypoint(now)
        if target!=plan.last_sent:
            self._write(device,target);plan.last_sent=target;self._update(last_sent=list(target))
        self._update(progress=min(.99,max(0,(now-plan.began)/plan.duration)))
        if now-plan.began>=plan.duration and plan.last_sent==plan.target:
            plan.phase='settling';plan.settle_at=time.monotonic();self._update(status='settling',message='目标已下发，等待实际位置到位')

    def check_diagnostics(self,diagnostics,errors=()):
        if not self._state.get('running'):return
        bad=diagnostics.get('hand_error_code',0) or any(any(v for v in r.values()) for r in diagnostics.get('motor_errors',[]))
        if bad or errors:
            reason='设备诊断出现错误标记' if bad else '运行中诊断未完整返回：'+'；'.join(errors)
            self._plan=None;self._update(armed=False,running=False,status='error',error=reason+'，已停止后续动作下发',message='设备异常，动作已停止下发')
            self._record('motion_fault',diagnostics=diagnostics,errors=list(errors))
