"""Default: preview only. --execute explicitly enables software control.

Run the dashboard first. It remains the only owner of the hand/adapter.
"""
import argparse
import json
import sys
from omnihand_usb.control_client import ControlClient


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='OmniHand position preview/control through local dashboard')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--preset',default='open')
    parser.add_argument('--mode',choices=('direct','smooth'),default='direct',help='Send one full target (default), or interpolate on the host')
    parser.add_argument('--duration',type=float,default=4,help='Host interpolation duration; only used with --mode smooth')
    parser.add_argument('--execute',action='store_true',help='Actually execute the selected position target')
    parser.add_argument('--enable-motors',action='store_true',help='Explicitly enable motors before executing (CAN FD/demo only)')
    args=parser.parse_args();client=ControlClient(args.port)
    preview=client.preview(preset=args.preset)
    print(json.dumps({'preset':args.preset,'positions':preview['positions'],'hardware_write':False},ensure_ascii=False,indent=2))
    if not args.execute:
        print('Preview only. No hardware write. Add --execute to run this target.');return
    client.arm()
    try:
        if args.enable_motors:client.enable()
        result=client.move(preset=args.preset,mode=args.mode,duration=args.duration)
        result=client.wait(result['motion_id'])
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        # During a running motion lock cancels waypoints and requests a current-pose hold.
        # Locking never switches off motor power.
        client.lock()


if __name__=='__main__':main()
