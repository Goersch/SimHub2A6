import tempfile
import unittest
from pathlib import Path

from SimHub2A6.LIB.simrig_load_calibration import (
    calibration_from_load_rates,
    load_calibration,
    save_load_calibration,
)


class SimRigLoadCalibrationTests(unittest.TestCase):
    def test_equal_loads_put_center_of_gravity_in_rig_center(self):
        calibration = calibration_from_load_rates((100, 100, 100, 100))

        self.assertEqual(calibration.normalized_loads, (0.25,) * 4)
        self.assertAlmostEqual(
            calibration.center_of_gravity_front_to_rear_mm, 0.0
        )
        self.assertAlmostEqual(
            calibration.center_of_gravity_left_to_right_mm, 0.0
        )

    def test_saved_load_values_are_loaded_and_recomputed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simrig_load_values.json"
            saved = save_load_calibration(
                (100, 300, 300, 100), path, activate=False
            )
            loaded = load_calibration(path)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.load_rates, saved.load_rates)
        self.assertAlmostEqual(sum(loaded.normalized_loads), 1.0)
        self.assertAlmostEqual(
            loaded.center_of_gravity_front_to_rear_mm, 0.0
        )
        self.assertAlmostEqual(
            loaded.center_of_gravity_left_to_right_mm, 247.5
        )

    def test_nonpositive_load_value_is_rejected(self):
        with self.assertRaises(ValueError):
            calibration_from_load_rates((100, 100, 0, 100))


if __name__ == "__main__":
    unittest.main()
