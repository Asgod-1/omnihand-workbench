"""O10 raw CAN FD reassembly, verified byte-for-byte against official SDK 1.1.11.

On O10 T1 / FW1.2.14, 268 uint8 values arrive in five frames. The first two
bytes are frame count and zero-based sequence; CAN FD padding is not a taxel.
This path opens no extra vendor SDK/USB context and performs every send on the
same owning thread as ordinary telemetry.
"""
from __future__ import annotations
import queue
import threading
import time
from .raw_bridge import EXPECTED_COUNTS

POINT_COUNT=sum(EXPECTED_COUNTS.values())
FRAME_COUNT=(POINT_COUNT+61)//62

def decode_fragments(parts):
    if set(parts)!=set(range(FRAME_COUNT)):
        raise ValueError('Incomplete raw tactile frame sequence')
    data=bytearray()
    for index in range(FRAME_COUNT):
        payload=bytes(parts[index])
        needed=min(62,POINT_COUNT-len(data))
        if len(payload)<2+needed or len(payload)>64 or payload[:2]!=bytes((FRAME_COUNT,index)):
            raise ValueError('Invalid or truncated raw tactile fragment')
        data.extend(payload[2:2+needed])
    regions={};offset=0
    for sensor_id,(name,count) in enumerate(EXPECTED_COUNTS.items(),1):
        regions[name]={'values':list(data[offset:offset+count]),'sensor_id':sensor_id}
        offset+=count
    return regions

class DirectRawReader:
    def __init__(self,device,channel,node_id,timeout=.35):
        if type(node_id) is not int or not 1<=node_id<=254:
            raise ValueError('Public hand ID must be an explicit unicast ID')
        self.device,self.channel,self.node_id=device,channel,node_id
        self.can_id=0x60100|node_id
        self.timeout=timeout
        self.owner_thread=threading.get_ident()
        self.queue=queue.Queue(maxsize=64)
        self.active=False
        self.closed=False

    def receive(self,can_id,payload):
        if self.active and can_id==self.can_id:
            try:self.queue.put_nowait((time.monotonic(),bytes(payload)))
            except queue.Full:pass

    def read(self):
        if self.closed or threading.get_ident()!=self.owner_thread:
            raise RuntimeError('Raw reader is closed or is outside the device thread')
        latest={}
        for attempt in (1,2):
            while True:
                try:self.queue.get_nowait()
                except queue.Empty:break
            parts={};start=time.monotonic();self.active=True
            try:
                # This is the explicit tactile GET opcode, never an empty frame.
                if not self.device.send_can(self.channel,self.can_id,1,b'\x00',canfd=True,ext=True,rtr=False,brs=True):
                    raise RuntimeError('Raw tactile GET was not submitted')
                until=start+self.timeout
                while time.monotonic()<until:
                    try:received,payload=self.queue.get(timeout=max(.001,until-time.monotonic()))
                    except queue.Empty:break
                    if received<start or len(payload)<2 or payload[0]!=FRAME_COUNT or payload[1]>=FRAME_COUNT:
                        continue
                    index=payload[1]
                    if index==0:parts={}
                    if not parts and index!=0:continue
                    if index in parts and parts[index]!=payload:
                        raise ValueError('Conflicting raw tactile fragments')
                    parts[index]=payload
                    if len(parts)==FRAME_COUNT:
                        regions=decode_fragments(parts)
                        return {'status':'ready','regions':regions,'returned_counts':{k:len(v['values']) for k,v in regions.items()},
                            'expected_counts':EXPECTED_COUNTS,'returned_frames':FRAME_COUNT,'sensor_count_reply':None,
                            'region_count':len(regions),'query_attempts':attempt,'request_encoding':'explicit_tactile_get_opcode',
                            'decoder':'project_o10_verified_against_official_1.1.11','unit':'uint8; force calibration not verified','error':None}
            finally:self.active=False
            latest=parts
        return {'status':'partial' if latest else 'no_response','regions':{},'returned_counts':{},'expected_counts':EXPECTED_COUNTS,
            'returned_frames':len(latest),'query_attempts':2,'error':'本次未收到完整的 5 个原始分包；不补零或使用旧帧。'}

    def close(self):
        self.closed=True;self.active=False
