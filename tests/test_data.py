import unittest

from omnihand_usb.device import integer_vector
from omnihand_usb.monitor import read_sample


class DataContractTests(unittest.TestCase):
    def test_measured_position_overshoot_is_preserved(self):
        values = [2048, 2014, 4069, 1992, 4116, 4125, 2009, 4100, 2057, 4103]
        self.assertEqual(integer_vector(values, expected=10), values)

    def test_empty_and_partial_replies_are_not_zero_padded(self):
        for values in ([], [0] * 9):
            with self.assertRaises(ValueError):
                integer_vector(values, expected=10)

    def test_tactile_encoding_is_checked(self):
        self.assertEqual(integer_vector([0, 255, 1], tactile=True), [0, 255, 1])
        for values in ([256], [-1], [True], ["0"]):
            with self.assertRaises(ValueError):
                integer_vector(values, tactile=True)

    def test_missing_tactile_region_has_error_not_a_zero_array(self):
        class Device:
            def positions(self):
                return [100] * 10

            def tactile(self, region):
                if region == "PALM":
                    raise TimeoutError("no reply")
                return [0] * 16

        sample = read_sample(Device(), diagnostics=False)
        self.assertIsNone(sample["tactile"]["PALM"]["values"])
        self.assertIn("no reply", sample["tactile"]["PALM"]["error"])
        self.assertEqual(sample["tactile"]["THUMB"]["values"], [0] * 16)


if __name__ == "__main__":
    unittest.main()
