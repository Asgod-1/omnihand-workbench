import math
import struct
import unittest
import importlib.util
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from omnihand_usb.raw_bridge import pack_frame,validate_read,EXPECTED_COUNTS
from omnihand_usb.raw_client import format_result
from omnihand_usb.kinematics import PoseSolver

class ReadOnlyRawContractTests(unittest.TestCase):
    def test_disabling_a_previously_verified_raw_reader_never_claims_live_data(self):
        from omnihand_usb.damiao import _NativeDamiaoHand
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'config.local.json').write_text('{"raw_tactile_probe_enabled":false}',encoding='utf-8')
            (root/'data').mkdir()
            (root/'data/raw_tactile_compatibility.json').write_text(json.dumps({
                'status':'ready','regions':{'THUMB':{'values':[1]*16}},
                'firmware_version':'1.2.14','hardware_version':'3.0.0','can_id':9
            }),encoding='utf-8')
            hand=_NativeDamiaoHand(root,node_id=9)
            hand.info={'firmware_version':'1.2.14','hardware_version':'3.0.0'}
            with patch('omnihand_usb.raw_tactile.DirectRawReader') as reader:
                result=hand.raw_tactile()
                self.assertEqual(result['status'],'disabled')
                self.assertEqual(result['last_probe_status'],'ready')
                self.assertEqual(result['regions'],{})
                reader.assert_not_called()

    def test_raw_queries_require_an_explicit_public_id_and_never_reuse_private_can_id(self):
        from omnihand_usb.damiao import _NativeDamiaoHand
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'config.local.json'
            config.write_text(json.dumps({'raw_tactile_probe_enabled':True}),encoding='utf-8')
            hand=_NativeDamiaoHand(folder,node_id=9)
            with patch('omnihand_usb.raw_tactile.DirectRawReader') as reader:
                self.assertEqual(hand.raw_tactile()['status'],'disabled')
                reader.assert_not_called()
                config.write_text(json.dumps({'raw_tactile_probe_enabled':True,'raw_public_hand_id':1}),encoding='utf-8')
                reader.return_value.read.return_value={'status':'ready','regions':{}}
                self.assertEqual(hand.raw_tactile()['public_hand_id'],1)
                reader.assert_called_once_with(None,0,1)

    def test_official_sdk_capture_matches_bridge_packet_exactly(self):
        # Captured offline from unmodified omnihand 1.1.11's raw getter.
        packet=pack_frame(0x00060109,bytes(64))
        self.assertEqual(packet.hex(),'55010000005000000000000000000006010901500040'+'00'*64+'1b')
        self.assertEqual(validate_read(packet[6:-1],9),(0x60109,bytes(64)))
        self.assertEqual(validate_read(pack_frame(0x60109,b'\x01')[6:-1],9),(0x60109,b'\x01'))

    def test_motor_writes_other_nodes_and_invalid_frames_are_rejected(self):
        for cid,data,ext in [(9,b'\x08'+bytes(20),False),(0x60108,bytes(64),True),(0x60109,b'\x40',True),(0x60109,b'\x02',True),(0x10109,bytes(64),True)]:
            with self.subTest(cid=cid,data=data.hex()):
                with self.assertRaises(ValueError): validate_read(pack_frame(cid,data,ext=ext)[6:-1],9)
        damaged=bytearray(pack_frame(0x60109,bytes(64))[6:-1]);damaged[15]=255
        with self.assertRaises(ValueError):validate_read(damaged,9)
        self.assertEqual(sum(EXPECTED_COUNTS.values()),268)

class RawArrayValidationTests(unittest.TestCase):
    def test_missing_region_is_never_presented_as_a_complete_or_zero_array(self):
        data=[SimpleNamespace(sensor_id=i,data=[i]*n) for i,n in enumerate(EXPECTED_COUNTS.values(),1)]
        valid=format_result(data,7)
        self.assertEqual(valid['status'],'ready')
        self.assertEqual(sum(len(r['values']) for r in valid['regions'].values()),268)
        partial=format_result(data[:-1],7)
        self.assertEqual(partial['status'],'partial')
        self.assertEqual(partial['regions'],{})
        data[0].data=[]
        with self.assertRaises(ValueError):format_result(data,7)

    def test_silent_sdk_result_has_no_measured_counts(self):
        result=format_result([],0)
        self.assertEqual(result['status'],'no_response')
        self.assertEqual(result['regions'],{})
        self.assertIsNone(result['sensor_count_reply'])

@unittest.skipUnless(importlib.util.find_spec('omnihand'), 'Official SDK required for kinematics verification')
class OfficialKinematicsTests(unittest.TestCase):
    def test_open_feedback_produces_extended_fingers(self):
        s=PoseSolver('right')
        result=s.solve([2048,2048,4096,2048,4096,4096,2048,4096,2048,4096])
        self.assertEqual(len(result['joints_rad']),17)
        for finger in ('index','middle','ring','pinky'):
            self.assertAlmostEqual(result['joints_rad']['R_'+finger+'_pip_joint'],0,places=4)
            self.assertAlmostEqual(result['joints_rad']['R_'+finger+'_dip_joint'],0,places=4)

    def test_passive_flexion_and_feedback_preservation(self):
        inputs=[2048,2048,0,2048,0,0,2048,0,2048,0]
        result=PoseSolver('right').solve(inputs)
        self.assertGreater(result['joints_rad']['R_index_pip_joint'],1.3)
        self.assertGreater(result['joints_rad']['R_index_dip_joint'],1)
        self.assertGreater(result['joints_rad']['R_thumb_pip_joint'],0)
        values=[4117]*10
        clamped=PoseSolver('right').solve(values)
        self.assertEqual(values,[4117]*10)
        self.assertEqual(clamped['clamped_for_display'],list(range(1,11)))
        self.assertTrue(all(math.isfinite(v) for v in clamped['joints_rad'].values()))
