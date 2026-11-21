import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock

from omnihand_usb.control_profile import control_profile,mixed_can_frames,joint_target
from omnihand_usb.motion_protocol import joint_payload,joint_reply
from omnihand_usb.damiao import _NativeDamiaoHand,DamiaoHand
from omnihand_usb.device import USBHand


class LiveProtocolTests(unittest.TestCase):
    def test_single_axis_private_command_and_reply_match_the_requested_axis(self):
        self.assertEqual(joint_payload(10,4096),b'\x06\x0a\x00\x10')
        self.assertEqual(joint_reply(b'\x06\x0a\xff\x0f',10),4095)
        for index,value in ((0,1),(11,1),(True,1),(1,4097),(1,1.5)):
            with self.assertRaises(ValueError):joint_target(index,value)
        with self.assertRaises(ValueError):joint_reply(b'\x06\x09\x00\x10',10)
        with self.assertRaises(ValueError):joint_reply(b'\x06\x0a\x00',10)

    def test_pt_encoding_matches_unmodified_sdk_capture_including_ma_boundaries(self):
        profile=control_profile({'control_mode':'pt','currents_ma':[0,1,100,200,500,999,1000,250,750,50]})
        frames=mixed_can_frames(list(range(100,110)),profile)
        captured='60640000006165000000626600190063670033006468007f00656900fe00666a00ff00676b003f00686c00bf00696d000c000000000000000000000000000000'
        self.assertEqual(frames,[bytes.fromhex(captured)])

    def test_pvt_encoding_covers_all_ten_axes_with_a_second_frame(self):
        profile=control_profile({'control_mode':'pvt','currents_ma':[0,1,100,200,500,999,1000,250,750,50],'velocities':list(range(-300,-290))})
        frames=mixed_can_frames(list(range(100,110)),profile)
        captured='a06400d4fe0000a16500d5fe0000a26600d6fe1900a36700d7fe3300a46800d8fe7f00a56900d9fefe00a66a00dafeff00a76b00dbfe3f00a86c00dcfebf0000'
        self.assertEqual(frames[0],bytes.fromhex(captured));self.assertEqual(len(frames),2)
        self.assertEqual(frames[1],b'\xa9'+struct.pack('<hhh',109,-291,12))

    def test_single_joint_mixed_frame_does_not_encode_the_other_nine_axes(self):
        profile={'control_mode':'pt','currents_ma':[500]*10}
        self.assertEqual(mixed_can_frames([1234]*10,profile,[9]),[b'\x69'+struct.pack('<hh',1234,127)])

    def test_unsupported_modes_and_invalid_mixed_parameters_are_rejected(self):
        for body in ({'control_mode':'torque'},{'control_mode':'velocity'},{'control_mode':'pt'},
                     {'control_mode':'pt','currents_ma':[1001]*10},
                     {'control_mode':'pvt','currents_ma':[100]*10,'velocities':[4097]*10}):
            with self.subTest(body=body),self.assertRaises(ValueError):control_profile(body)

    def test_native_joint_write_requires_correct_ack_and_fresh_error_check(self):
        hand=_NativeDamiaoHand('.');hand._exchange=Mock(return_value=b'\x06\x03\xd2\x04')
        hand._query=Mock(return_value=b'\x0d\x00\x00');hand.positions=Mock(return_value=[1200]*10)
        result=hand.set_joint_position(3,1234)
        self.assertEqual(hand._exchange.call_args.args[0],joint_payload(3,1234));self.assertEqual(result['hand_error_code'],0)
        hand.positions.reset_mock()
        result=hand.set_joint_position(3,1234,readback=False)
        self.assertEqual(result['ack_joint_position'],1234)
        self.assertNotIn('positions',result)
        hand.positions.assert_not_called()
        hand._exchange.return_value=b'\x06\x04\xd2\x04'
        with self.assertRaises(ValueError):hand.set_joint_position(3,1234)

    def test_native_mixed_uses_explicit_public_id_and_keeps_readback_distinct_from_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);hand=_NativeDamiaoHand(root);hand._device=Mock();hand._device.send_can.return_value=True
            hand._query=Mock(return_value=b'\x0d\x00\x00');hand.positions=Mock(return_value=[1200]*10)
            profile={'control_mode':'pvt','currents_ma':[100]*10,'velocities':[500]*10}
            with self.assertRaises(ValueError):hand.set_mixed([2000]*10,profile)
            hand._device.send_can.assert_not_called()
            (root/'config.local.json').write_text(json.dumps({'raw_public_hand_id':1}))
            reply=hand.set_mixed([2000]*10,profile)
            self.assertEqual(hand._device.send_can.call_count,2)
            self.assertTrue(all(c.args[1]==0x00140181 and c.args[4:]==(True,True,False,True) for c in hand._device.send_can.call_args_list))
            self.assertEqual(reply['delivery'],'transport_submitted_with_readback');self.assertNotIn('response_hex',reply)
            hand._owner_thread=threading.get_ident()+1
            with self.assertRaises(RuntimeError):hand.set_mixed([2000]*10,profile)

    def test_proxy_forwards_only_validated_joint_and_profile_operations(self):
        hand=DamiaoHand('.');hand._rpc=Mock(return_value={'positions':[0]*10})
        hand.set_joint_position(2,1234);self.assertEqual(hand._rpc.call_args.args,('set_joint_position',))
        with self.assertRaises(ValueError):hand.set_joint_position(12,1234)
        self.assertEqual(hand._rpc.call_count,1)

    def test_usb_single_axis_uses_sdk_single_joint_method_and_fresh_readback(self):
        hand=USBHand.__new__(USBHand);hand._hand=Mock();hand._hand.get_all_joint_positions.return_value=[1200]*10
        result=hand.set_joint_position(6,1234)
        hand._hand.set_joint_position.assert_called_once_with(6,1234);hand._hand.set_all_joint_positions.assert_not_called()
        self.assertEqual(result['positions'],[1200]*10)


if __name__=='__main__':unittest.main()
