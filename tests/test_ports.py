from types import SimpleNamespace
import unittest
from unittest.mock import patch

from omnihand_usb.device import resolve_port


class PortSelectionTests(unittest.TestCase):
    def setUp(self):
        self.bridge = SimpleNamespace(device="COM8", description="USB UART", vid=0x34B7, pid=0x6632)
        self.hand = SimpleNamespace(device="COM5", description="USB CDC", vid=0x0483, pid=0x5740)

    def test_auto_does_not_treat_adapter_uart_as_a_hand(self):
        with patch("serial.tools.list_ports.comports", return_value=[self.bridge, self.hand]):
            self.assertEqual(resolve_port("auto"), "COM5")

    def test_only_adapter_uart_does_not_auto_open(self):
        with patch("serial.tools.list_ports.comports", return_value=[self.bridge]):
            with self.assertRaises(RuntimeError):
                resolve_port("auto")

    def test_explicit_adapter_uart_is_also_rejected(self):
        with patch("serial.tools.list_ports.comports", return_value=[self.bridge]):
            with self.assertRaises(ValueError):
                resolve_port("COM8")


if __name__ == "__main__":
    unittest.main()
