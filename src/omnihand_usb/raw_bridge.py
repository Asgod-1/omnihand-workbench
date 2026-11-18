"""Local, read-only ZLG network framing adapter for the official O10 raw decoder.

The official SDK owns raw multi-frame decoding. Only requests observed from its
get_num_of_tactile_sensors / get_all_tactile_sensor_data_raw getters are allowed.
This bridge does not expose a general CAN transmitter.
"""
from __future__ import annotations
import json
from pathlib import Path
import queue
import socket
import struct
import subprocess
import sys
import threading
import time

EXPECTED_COUNTS=dict(zip(('THUMB','INDEX','MIDDLE','RING','LITTLE','PALM','DORSUM'),(16,18,18,18,18,78,102)))

def pack_frame(can_id, payload, *, ext=True):
    if len(payload)>64: raise ValueError('CAN FD payload too long')
    # ZLG CAN FD network record: timestamp, ID, flags, channel, byte length, data.
    data=struct.pack('>QIHBB',0,can_id,0x150 if ext else 0x110,0,len(payload))+bytes(payload).ljust(64,b'\0')
    packet=b'\x55\x01\0\0'+len(data).to_bytes(2,'big')+data
    checksum=0
    for b in packet: checksum^=b
    return packet+bytes((checksum,))

def validate_read(record, node_id):
    if len(record)!=80: raise ValueError('Expected one 80-byte CAN FD record')
    _,can_id,flags,channel,length=struct.unpack('>QIHBB',record[:16])
    payload=record[16:16+length]
    if can_id!=(0x00060100|node_id) or flags!=0x150 or channel!=0 or length not in (1,64):
        raise ValueError('Only the known O10 tactile read address and data-frame flags are permitted')
    if payload!=b'\x01' and payload!=bytes(64):
        raise ValueError('Only official tactile sensor-count and raw-array read requests are permitted')
    return can_id,payload

def wire_read_payload(payload):
    """Use an explicit GET opcode on the verified legacy Damiao adapter.

    Its 64-byte send acknowledgements report length zero and raw reads can stop
    replying. Keeping opcode 00 and omitting zero padding gives a one-byte ACK
    and complete replies on O10 FW1.2.14. Never emit an empty CAN request.
    """
    if payload==bytes(64): return b'\x00'
    if payload==b'\x01': return payload
    raise ValueError('Only the two verified tactile GET payloads are permitted')

class RawReader:
    def __init__(self, device, channel, node_id):
        if not 1<=node_id<=254: raise ValueError('Public SDK requires an 8-bit unicast hand ID')
        self.device,self.channel,self.node_id=device,channel,node_id
        self.server=socket.socket()
        self.server.bind(('127.0.0.1',0))
        self.server.listen(1)
        self.server.settimeout(.2)
        self.connection=None
        self.owner_thread=threading.get_ident()
        self.stop=threading.Event()
        self.replies=queue.Queue(maxsize=256)
        self.requests=queue.Queue(maxsize=16)
        self.results=queue.Queue(maxsize=4)
        self.errors=[]
        self.thread=threading.Thread(target=self._serve,daemon=True,name='sdk-raw-read-bridge')
        self.thread.start()
        self.writer=threading.Thread(target=self._write,daemon=True,name='sdk-raw-replies')
        self.writer.start()
        command=[sys.executable,'-u','-m','omnihand_usb.raw_client',str(self.server.getsockname()[1]),str(node_id)]
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.output_reader=threading.Thread(target=self._read_output,daemon=True,name='sdk-raw-output')
        self.output_reader.start()

    def _serve(self):
        buf=b''
        try:
            while not self.stop.is_set() and self.connection is None:
                try: self.connection,_=self.server.accept()
                except socket.timeout: continue
            if self.connection is None: return
            self.connection.settimeout(.2)
            self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            while not self.stop.is_set():
                try: data=self.connection.recv(4096)
                except socket.timeout: continue
                if not data: break
                buf+=data
                while len(buf)>=6:
                    length=int.from_bytes(buf[4:6],'big')
                    if buf[0]!=0x55 or length>4096: raise ValueError('Invalid SDK bridge packet')
                    if len(buf)<length+7: break
                    packet,buf=buf[:length+7],buf[length+7:]
                    checksum=0
                    for b in packet: checksum^=b
                    if checksum: raise ValueError('Invalid SDK bridge checksum')
                    if packet[1]==0x50:
                        # SDK's local adapter initialization; never forwarded to the hand.
                        continue
                    if packet[1]!=1 or length!=80: raise ValueError('Unsupported SDK packet')
                    cid,payload=validate_read(packet[6:-1],self.node_id)
                    # USB writes belong to the same thread as ordinary queries.
                    # The socket thread only validates and queues requests.
                    self.requests.put_nowait((cid,payload))
        except queue.Full:
            if not self.stop.is_set(): self.errors.append('Raw transmit queue full')
        except (OSError,ValueError,RuntimeError) as exc:
            if not self.stop.is_set(): self.errors.append(str(exc))

    def receive(self, can_id, payload):
        try: self.replies.put_nowait(pack_frame(can_id,payload))
        except queue.Full: self.errors.append('Raw receive queue full')

    def _write(self):
        while not self.stop.is_set():
            try: packet=self.replies.get(timeout=.2)
            except queue.Empty: continue
            if self.connection is not None:
                try: self.connection.sendall(packet)
                except OSError:
                    if not self.stop.is_set(): self.errors.append('SDK raw reply connection closed')

    def _read_output(self):
        try:
            for line in self.process.stdout:
                if line.startswith('RAW_RESULT:'):
                    self.results.put_nowait(json.loads(line[len('RAW_RESULT:'):]))
        except (OSError,ValueError,queue.Full) as exc:
            if not self.stop.is_set(): self.errors.append(str(exc))

    def read(self):
        if threading.get_ident()!=self.owner_thread:
            raise RuntimeError('Raw reads must use the device-owning thread')
        deadline=time.monotonic()+3
        for attempt in (1,2):
            if self.process.poll() is not None: raise RuntimeError('Official raw decoder process exited')
            self.process.stdin.write('read\n');self.process.stdin.flush()
            while time.monotonic()<deadline:
                while True:
                    try: cid,payload=self.requests.get_nowait()
                    except queue.Empty: break
                    wire_payload=wire_read_payload(payload)
                    if not self.device.send_can(self.channel,cid,len(wire_payload),wire_payload,canfd=True,ext=True,rtr=False,brs=True):
                        raise RuntimeError('Official raw read was not submitted')
                try: result=self.results.get(timeout=.005)
                except queue.Empty:
                    if self.errors: raise RuntimeError(self.errors[-1])
                    if self.process.poll() is not None: raise RuntimeError('Official raw decoder process exited')
                    continue
                break
            else: raise TimeoutError('Official raw decoder did not return within three seconds')
            if self.errors: result['bridge_errors']=self.errors[-3:]
            result['query_attempts']=attempt
            result['request_encoding']='explicit_tactile_get_opcode'
            if result.get('status') not in ('no_response','partial') or self.errors: return result
        return result

    def close(self):
        process=getattr(self,'process',None)
        if process is not None:
            try:
                process.stdin.close()
                process.wait(timeout=1)
            except (OSError,subprocess.TimeoutExpired):
                process.terminate()
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired: process.kill();process.wait(timeout=1)
            if process.stdout: process.stdout.close()
        self.stop.set()
        if self.connection is not None:
            try: self.connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.connection.close()
        self.server.close()
        self.thread.join(timeout=1)
        self.writer.join(timeout=1)
        self.output_reader.join(timeout=1)
