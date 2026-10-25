import unittest
import numpy as np
from qt_waveform import x_coordinates


class AxisTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"t": 1.0, "sample_index": 20, "device_ms": 1000, "values": {"x": 5}},
                     {"t": 1.0001, "sample_index": 21, "device_ms": 1005, "values": {"x": 100}},
                     {"t": 3.0, "sample_index": 22, "device_ms": 1010, "values": {"x": 6}}]

    def test_sample_axis_separates_batched_points_without_changing_values(self):
        x, mode = x_coordinates(self.rows, 0)
        np.testing.assert_array_equal(x, [20, 21, 22])
        self.assertEqual(mode, "samples")
        self.assertEqual(self.rows[1]["values"]["x"], 100)

    def test_host_axis_preserves_real_gap(self):
        x, mode = x_coordinates(self.rows, 1)
        self.assertAlmostEqual(x[2] - x[1], 1.9999)
        self.assertEqual(mode, "host_s")

    def test_device_axis_uses_actual_timestamp(self):
        x, mode = x_coordinates(self.rows, 2)
        np.testing.assert_allclose(x, [1, 1.005, 1.01])
        self.assertEqual(mode, "device_s")

    def test_missing_device_clock_falls_back_to_host(self):
        del self.rows[1]["device_ms"]
        _, mode = x_coordinates(self.rows, 2)
        self.assertEqual(mode, "host_s")


if __name__ == "__main__":
    unittest.main()
