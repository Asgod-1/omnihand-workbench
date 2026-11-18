"""O10 mixed control parameters and public CAN encoding, checked against SDK 1.1.11."""
import struct
from .motion_protocol import target_vector
from .canfd_protocol import DLC_LENGTHS


def integer_values(values,low,high,label,count=10):
    if not isinstance(values,(list,tuple)) or len(values)!=count or any(type(v) is not int or not low<=v<=high for v in values):
        raise ValueError(f'{label}需要{count}个{low}–{high}的整数')
    return list(values)


def joint_target(index,position):
    integer_values([index],1,10,'关节编号',1);integer_values([position],0,4096,'目标位置',1)
    return index,position


def control_profile(body):
    mode=body.get('control_mode','position')
    if mode not in ('position','pt','pvt'):raise ValueError('O10仅提供position、pt、pvt控制组合，不支持纯力矩或独立速度模式')
    profile={'control_mode':mode}
    if mode!='position':profile['currents_ma']=integer_values(body.get('currents_ma'),0,1000,'电流mA')
    if mode=='pvt':profile['velocities']=integer_values(body.get('velocities'),-4095,4096,'速度原始值')
    return profile


def mixed_can_frames(positions,profile,indices=None):
    """Same records as official MixControlByPT/PVT; PVT split 9+1 for all ten axes.

    SDK 1.1.11 batch PVT was observed to emit only the first nine records.
    Torque in its API is mA, encoded as floor(mA*255/1000), not N.m.
    """
    positions=target_vector(positions);profile=control_profile(profile)
    mode=profile['control_mode']
    if mode=='position':raise ValueError('Mixed frames require pt or pvt')
    indices=list(range(10)) if indices is None else list(indices)
    if not indices or len(set(indices))!=len(indices):raise ValueError('关节索引不能为空或重复')
    records=[]
    for i in indices:
        integer_values([i],0,9,'报文关节索引',1)
        record=bytes(((0x60 if mode=='pt' else 0xA0)|i,))+struct.pack('<h',positions[i])
        if mode=='pvt':record+=struct.pack('<h',profile['velocities'][i])
        record+=struct.pack('<h',profile['currents_ma'][i]*255//1000)
        records.append(record)
    per_frame=12 if mode=='pt' else 9
    payloads=[]
    for start in range(0,len(records),per_frame):
        body=b''.join(records[start:start+per_frame]);length=next(n for n in DLC_LENGTHS if n>=len(body))
        payloads.append(body.ljust(length,b'\0'))
    return payloads
