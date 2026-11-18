import io
import json
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from omnihand_usb.damiao import _NativeDamiaoHand
from omnihand_usb.raw_bridge import RawReader, pack_frame, wire_read_payload


class RawTransportRecoveryTests(unittest.TestCase):
    def test_socket_request_waits_for_device_owner_before_any_usb_write(self):
        """A real socket thread may parse frames, but cannot call the USB SDK."""
        reader = RawReader.__new__(RawReader)
        reader.channel, reader.node_id = 0, 1
        reader.owner_thread = threading.get_ident()
        reader.stop = threading.Event()
        reader.requests, reader.results = queue.Queue(), queue.Queue()
        reader.errors, reader.connection = [], None
        reader.server = socket.socket()
        reader.server.bind(('127.0.0.1', 0))
        reader.server.listen(1)
        reader.server.settimeout(.1)
        writes = []

        def send_can(channel, can_id, length, payload, **flags):
            writes.append((threading.get_ident(), can_id, bytes(payload)))
            reader.results.put({'status': 'ready', 'regions': {}})
            return True

        reader.device = SimpleNamespace(send_can=send_can)
        reader.process = SimpleNamespace(poll=lambda: None, stdin=io.StringIO())
        worker = threading.Thread(target=reader._serve, daemon=True)
        worker.start()
        client = socket.create_connection(reader.server.getsockname(), timeout=1)
        try:
            client.sendall(pack_frame(0x60101, bytes(64)))
            until = time.monotonic()+1
            while reader.requests.empty() and time.monotonic() < until:
                time.sleep(.005)
            self.assertFalse(reader.requests.empty(), reader.errors)
            self.assertEqual(writes, [])
            self.assertEqual(reader.read()['status'], 'ready')
            self.assertEqual(writes, [(reader.owner_thread, 0x60101, b'\x00')])
        finally:
            reader.stop.set()
            client.close()
            if reader.connection is not None:
                reader.connection.close()
            reader.server.close()
            worker.join(timeout=1)

    def test_legacy_wire_request_keeps_opcode_and_never_becomes_empty(self):
        self.assertEqual(wire_read_payload(bytes(64)), b'\x00')
        self.assertEqual(wire_read_payload(b'\x01'), b'\x01')
        for invalid in (b'', b'\x08', b'\x00'+b'\x01'*63):
            with self.subTest(payload=invalid):
                with self.assertRaises(ValueError):
                    wire_read_payload(invalid)

    def test_one_missed_sample_does_not_disable_a_verified_raw_interface(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'config.local.json').write_text(json.dumps({
                'raw_tactile_probe_enabled': True, 'raw_public_hand_id': 1
            }), encoding='utf-8')
            hand = _NativeDamiaoHand(folder)
            with patch('omnihand_usb.raw_tactile.DirectRawReader') as factory:
                reader = factory.return_value
                reader.node_id = 1
                reader.read.side_effect = [
                    {'status': 'ready', 'regions': {}},
                    {'status': 'no_response', 'regions': {}},
                    {'status': 'ready', 'regions': {}}
                ]
                with patch('omnihand_usb.damiao.time.monotonic', return_value=100):
                    self.assertEqual(hand.raw_tactile()['status'], 'ready')
                    missed = hand.raw_tactile()
                    self.assertEqual(missed['status'], 'no_response')
                    self.assertEqual(missed['regions'], {})
                    self.assertEqual(missed['consecutive_failures'], 1)
                    reader.close.assert_not_called()
                with patch('omnihand_usb.damiao.time.monotonic', return_value=101):
                    self.assertEqual(hand.raw_tactile()['status'], 'ready')
                self.assertEqual(reader.read.call_count, 3)


if __name__ == '__main__':
    unittest.main()
