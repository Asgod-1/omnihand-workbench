"""Separate official-SDK process: native Python bindings hold the GIL during reads."""
import json
import os
import sys
from .raw_bridge import EXPECTED_COUNTS

def format_result(data, sensor_count=None):
    regions={}
    for item in data:
        sensor_id=int(item.sensor_id)
        if not 1<=sensor_id<=7: raise ValueError('Unknown raw tactile sensor ID')
        name=list(EXPECTED_COUNTS)[sensor_id-1]
        values=list(item.data)
        if name in regions: raise ValueError('Duplicate raw tactile sensor')
        if len(values)!=EXPECTED_COUNTS[name] or any(type(x) is not int or not 0<=x<=255 for x in values):
            raise ValueError(f'Raw sensor {name} returned an unexpected array; no padding applied')
        regions[name]={'values':values,'sensor_id':sensor_id}
    complete=len(regions)==7
    return {'status':'ready' if complete else 'partial' if regions else 'no_response',
            'regions':regions if complete else {},'returned_counts':{k:len(v['values']) for k,v in regions.items()},
            'sensor_count_reply':sensor_count or None,'expected_counts':EXPECTED_COUNTS,
            'error':None if complete else '官方公有 CAN 原始读取未返回完整阵列；需要确认这只手的协议、节点 ID 和固件是否支持该接口。',
            'decoder':'official_omnihand_1.1.11','unit':'uint8; SDK documents 1g/count, calibration not verified'}

def main():
    from omnihand import OmniHand2025,HandType
    hand=OmniHand2025.create_hand_by_zlgcan_tcp(HandType.RIGHT,int(sys.argv[2]),'127.0.0.1',int(sys.argv[1]),0)
    if not hand.init(): raise RuntimeError('Official decoder initialization failed')
    sensor_count=None
    for line in sys.stdin:
        if line.strip()!='read': break
        try:
            if sensor_count is None: sensor_count=int(hand.get_num_of_tactile_sensors())
            data=list(hand.get_all_tactile_sensor_data_raw())
            result=format_result(data,sensor_count)
        except Exception as exc:
            result={'status':'error','regions':{},'expected_counts':EXPECTED_COUNTS,'error':str(exc)}
        print('RAW_RESULT:'+json.dumps(result,ensure_ascii=True),flush=True)
    sys.stdout.flush()
    os._exit(0)  # Bounded SDK teardown; sockets are closed by the OS.

if __name__=='__main__': main()
