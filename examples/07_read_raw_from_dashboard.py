"""Read one genuine raw array from the running dashboard, without opening USB.

Start Start_CANFD_Dashboard.cmd first. This example can share the dashboard's
existing connection; it never opens another vendor SDK context or sends CAN.
"""
import argparse
import json
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--http-port',type=int,default=8765)
    args=parser.parse_args()
    if not 1<=args.http_port<=65535:
        parser.error('HTTP port must be between 1 and 65535')
    with urllib.request.urlopen(f'http://127.0.0.1:{args.http_port}/api/state',timeout=5) as response:
        state=json.load(response)
    raw=state.get('raw_tactile',{})
    if not state.get('connected') or state.get('mode')!='canfd' or raw.get('status')!='ready':
        raise RuntimeError('Dashboard must be connected over CAN FD with a complete raw array')
    expected={'THUMB':16,'INDEX':18,'MIDDLE':18,'RING':18,'LITTLE':18,'PALM':78,'DORSUM':102}
    counts={name:len(region.get('values',[])) for name,region in raw.get('regions',{}).items()}
    if counts!=expected:
        raise RuntimeError(f'Incomplete O10 T1 raw array: {counts}')
    print(json.dumps({'sampled_at':raw['sampled_at'],'returned_counts':counts,
        'total_points':sum(counts.values()),'regions':raw['regions']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
