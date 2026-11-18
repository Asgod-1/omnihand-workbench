"""Documented O10 private position/power writes, separate from read commands.

Official O10 user manual sections 4.3 / command table: standard FD+BRS,
0x08 + ten little-endian uint16 targets; 0x01 + 0/1 power state.
No calibration, mode, current, ID, persistent-save or arbitrary CAN interface.
"""
import struct
from .canfd_protocol import DLC_LENGTHS,response_body


def target_vector(values):
    if not isinstance(values,(list,tuple)) or len(values)!=10:
        raise ValueError('需要十个关节目标值')
    if any(type(v) is not int or not 0<=v<=4096 for v in values):
        raise ValueError('关节目标必须是0–4096的整数，不能使用布尔值或小数')
    return list(values)


def position_payload(values):
    payload=b'\x08'+struct.pack('<10H',*target_vector(values))
    # 21 meaningful bytes occupy a legal 24-byte CAN FD payload.
    length=next(n for n in DLC_LENGTHS if n>=len(payload))
    return payload.ljust(length,b'\0')


def power_payload(enabled):
    if type(enabled) is not bool:
        raise ValueError('只允许明确的使能或失能，不允许校准模式')
    return bytes((0x01,int(enabled)))


def position_reply(payload):
    body=response_body(payload,0x08,60)
    return {'positions':list(struct.unpack('<10H',body[:20])),
            'velocities_raw':list(struct.unpack('<10h',body[20:40])),
            'torques_raw':list(body[40:50]),'faults':list(body[50:60])}


def power_reply(payload):
    if response_body(payload,0x01,1)[0]!=1:
        raise RuntimeError('设备未确认电机电源指令成功')


def power_state(payload):
    state=response_body(payload,0x02,1)[0]
    if state not in (0,1,2):raise ValueError('电机状态回复不合法')
    return state
