import struct
import unittest
from unittest.mock import Mock

from omnihand_usb import canfd_protocol as protocol
from omnihand_usb.damiao import _NativeDamiaoHand


class CanFdContractTests(unittest.TestCase):
    def test_motor_writes_and_calibration_are_not_constructible(self):
        for command in (0x01, 0x06, 0x08, 0x0E, 0x32, 0x80, True, 256):
            with self.subTest(command=command), self.assertRaises(ValueError):
                protocol.query_payload(command)
        self.assertEqual(protocol.query_payload(0x09), b"\x09")

    def test_padded_position_reply_keeps_all_ten_measured_values(self):
        values = [0, 2048, 4096, 4116, 4125, 10, 20, 30, 40, 50]
        payload = b"\x09" + struct.pack("<10H", *values) + bytes(3)
        self.assertEqual(protocol.positions(payload), values)

    def test_short_and_wrong_command_replies_are_rejected(self):
        with self.assertRaises(ValueError):
            protocol.positions(b"\x09" + bytes(19))
        with self.assertRaises(ValueError):
            protocol.positions(b"\x08" + bytes(23))
        with self.assertRaises(ValueError):
            protocol.response_body(b"\x12" + bytes(47), 0x12, 48)

    def test_current_stays_raw_and_temperature_is_signed(self):
        self.assertEqual(protocol.current_raw(b"\x0a" + struct.pack("<10H", *([9] * 10)) + bytes(3)), [9] * 10)
        self.assertEqual(protocol.temperatures(b"\x0c" + bytes([255] + [30] * 9) + bytes(1)), [-1] + [30] * 9)

    def test_variant_and_versions_are_decoded_and_other_models_rejected(self):
        info = protocol.vendor_info(bytes.fromhex("cd0254310101090300000a00"))
        self.assertEqual(info["firmware_version"], "1.1.9")
        self.assertEqual(info["product_variant"], "T1")
        with self.assertRaises(ValueError):
            protocol.vendor_info(bytes.fromhex("cd0354310101090300000a00"))

    def test_tactile_group_slices_yield_130_points(self):
        groups = {0x12: bytes(range(48)), 0x13: bytes(range(32)), 0x14: bytes(range(50))}
        extracted = {}
        for name, (cmd, length, begin, end) in protocol.TACTILE_SLICES.items():
            body = protocol.response_body(bytes([cmd]) + groups[cmd] + bytes(64 - length - 1), cmd, length)
            extracted[name] = list(body[begin:end])
        self.assertEqual(sum(map(len, extracted.values())), 130)
        self.assertEqual(extracted["INDEX"], list(range(16, 32)))
        self.assertEqual(extracted["DORSUM"], list(range(25, 50)))

    def test_close_uses_context_owner_once_without_double_device_close(self):
        hand = _NativeDamiaoHand(".")
        context, device = Mock(), Mock()
        hand._context, hand._device, hand._enabled = context, device, True
        hand.close()
        hand.close()
        device.enable_channel.assert_called_once_with(0, False)
        device.close.assert_not_called()
        context.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
