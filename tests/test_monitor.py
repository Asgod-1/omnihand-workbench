from pathlib import Path
import tempfile
import time
import unittest

from omnihand_usb.monitor import Monitor,read_sample
from omnihand_usb.device import DemoHand
from unittest.mock import Mock


class MonitorTests(unittest.TestCase):
    def test_canfd_never_polls_ordinary_tactile_or_reuses_old_ordinary_data(self):
        hand=DemoHand();hand.mode='canfd';hand.tactile=Mock(side_effect=AssertionError('ordinary query must not be sent'))
        sample=read_sample(hand)
        hand.tactile.assert_not_called();self.assertEqual(sample['tactile'],{})
        self.assertEqual(sample['raw_tactile']['status'],'ready')
        with tempfile.TemporaryDirectory() as temp:
            monitor=Monitor(Path(temp));monitor._state['tactile']={'INDEX':{'values':[1]*16}}
            monitor._merge(sample);self.assertEqual(monitor.snapshot()['tactile'],{})
    def test_failed_position_read_does_not_advance_last_good_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            monitor = Monitor(Path(temp))
            monitor._merge({"sampled_at": "start1", "completed_at": "good-time", "positions": [4116] * 10, "errors": [], "tactile": {}})
            monitor._merge({"sampled_at": "start2", "completed_at": "bad-time", "positions": None, "errors": ["timeout"], "tactile": {}})
            state = monitor.snapshot()
            self.assertEqual(state["positions_at"], "good-time")
            self.assertEqual(state["valid_position_samples"], 1)
            self.assertEqual(state["positions"], [4116] * 10)
            self.assertEqual(state["errors"], ["timeout"])

    def test_demo_recording_and_disconnect_release_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            monitor = Monitor(Path(temp), interval=.1)
            try:
                monitor.connect(demo=True)
                deadline = time.monotonic() + 2
                while monitor.snapshot()["valid_position_samples"] == 0 and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertGreater(monitor.snapshot()["valid_position_samples"], 0)
                state = monitor.set_recording(True)
                path = Path(temp) / state["record_path"]
                self.assertTrue(path.is_file())
                self.assertEqual(state["mode"], "demo")
                monitor.disconnect()
                state = monitor.snapshot()
                self.assertFalse(state["connected"])
                self.assertFalse(state["recording"])
                # A closed writer permits an immediate rename on Windows.
                path.rename(path.with_suffix(".closed"))
            finally:
                monitor.disconnect()


if __name__ == "__main__":
    unittest.main()
