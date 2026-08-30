import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from SimHub2A6.Analyse import (
    SIGNAL_ACTUAL,
    SIGNAL_CALCULATED_ACTUAL,
    SIGNAL_GROUND_DEVIATION,
    SIGNAL_TARGET,
    load_recording,
)
from SimHub2A6.LIB import a6_simu
from SimHub2A6.LIB.a6_simu import (
    A6Simulator,
    calculate_simrig_state,
    simrig_emu,
)


class A6SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.simulator = A6Simulator(axis_count=2)

    def test_target_remains_pending_until_planner_trigger(self):
        self.simulator.set_target(1, 25.0, 400, 100.0, 500, raw_target=50000)
        self.assertEqual(self.simulator.snapshot()[0], (0.0, 0.0, 0.0))

        self.simulator.planner_trigger(0)
        self.assertEqual(self.simulator.snapshot()[0][0], 25.0)

    def test_simrig_emu_reports_no_gap_for_a_plane(self):
        # A constant height is a valid rigid plane independent of rig geometry.
        deviations = simrig_emu((70.0, 70.0, 70.0, 70.0))
        self.assertTrue(all(abs(deviation) < 1e-9 for deviation in deviations))

    def test_simrig_emu_reports_signed_rear_deviations(self):
        rig_state = calculate_simrig_state((10.0, 0.0, 10.0, 0.0))
        deviations = rig_state.ground_deviations_mm
        self.assertAlmostEqual(deviations[0], 0.0)
        self.assertAlmostEqual(deviations[1], 0.0)
        self.assertAlmostEqual(deviations[2], -10.0)
        self.assertAlmostEqual(deviations[3], 10.0)
        self.assertEqual(rig_state.lifted_axis, 7)
        self.assertAlmostEqual(rig_state.ground_loads[3], 0.0)
        self.assertAlmostEqual(sum(rig_state.ground_loads), 1.0)

    def test_centered_rig_shares_planar_load_equally(self):
        with patch.object(
            a6_simu, "get_active_center_of_gravity", return_value=(0.0, 0.0)
        ):
            rig_state = calculate_simrig_state((0.0, 0.0, 0.0, 0.0))
        self.assertIsNone(rig_state.lifted_axis)
        for load in rig_state.ground_loads:
            self.assertAlmostEqual(load, 0.25)

    def test_runtime_load_calibration_changes_support_loads(self):
        with patch.object(
            a6_simu,
            "get_active_center_of_gravity",
            return_value=(0.0, 247.5),
        ):
            rig_state = calculate_simrig_state((0.0, 0.0, 0.0, 0.0))

        left_load = rig_state.ground_loads[0] + rig_state.ground_loads[3]
        right_load = rig_state.ground_loads[1] + rig_state.ground_loads[2]
        self.assertAlmostEqual(left_load, 0.25)
        self.assertAlmostEqual(right_load, 0.75)

    def test_game_start_uses_last_triggered_target_as_actual_position(self):
        self.simulator.set_target(1, 10.0, 400, 100.0, 400, raw_target=40000)
        self.simulator.planner_trigger(0)
        self.simulator.set_target(1, 30.0, 400, 100.0, 400, raw_target=50000)

        # Avoid creating a recording in this unit test.
        self.simulator._start_recording_locked = lambda: None
        self.simulator.set_game_running(True)

        self.assertEqual(self.simulator.snapshot()[0], (10.0, 10.0, 0.0))

    def test_axis_accelerates_toward_active_target(self):
        self.simulator.set_target(1, 20.0, 400, 100.0, 400, raw_target=50000)
        self.simulator.planner_trigger(0)
        state = self.simulator._states[0]
        self.simulator._advance_axis(state, 0.05)

        self.assertGreater(state.calculated_actual_position_mm, 0.0)
        self.assertGreater(state.actual_velocity_mm_s, 0.0)
        self.assertLessEqual(state.actual_velocity_mm_s, 100.0)

    def test_recording_contains_simulation_values_and_remains_loadable(self):
        simulator = A6Simulator(axis_count=7)
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            a6_simu, "SIMHUB_DATA_DIR", Path(temp_dir)
        ):
            simulator.set_target(4, 80.0, 400, 100.0, 400, raw_target=40000)
            simulator.planner_trigger(0)
            simulator.set_game_running(True)
            simulator.set_read_actual_positions_enabled(True)
            simulator.set_read_actual_position(4, 9.5)
            simulator.set_target(1, 10.0, 400, 100.0, 400, raw_target=40000)
            simulator.planner_trigger(0)
            with simulator._lock:
                simulator._advance_axis(simulator._states[0], 0.05)
                simulator._record_snapshot_locked()
            simulator.set_game_running(False)

            recording = next(Path(temp_dir).glob("*.csv"))
            with recording.open("r", encoding="utf-8-sig", newline="") as csv_file:
                row = next(csv.DictReader(csv_file, delimiter=";"))
            self.assertIn("TargetPosition1", row)
            self.assertIn("CalculatedActualPosition1", row)
            self.assertIn("ActualPosition1", row)
            self.assertIn("ActualPositionTimestamp1", row)
            self.assertIn("ActualPositionAgeMs1", row)
            self.assertIn("ActualVelocity1", row)
            self.assertIn("GroundDeviation4", row)
            self.assertIn("GroundLoad4", row)
            self.assertAlmostEqual(
                sum(float(row[f"GroundLoad{axis}"]) for axis in range(4, 8)),
                1.0,
            )
            self.assertEqual(float(row["TargetPosition4"]), 10.0)
            self.assertEqual(float(row["CalculatedActualPosition4"]), 10.0)
            self.assertEqual(float(row["ActualPosition4"]), 9.5)
            self.assertNotEqual(row["ActualPositionTimestamp4"], "")
            self.assertGreaterEqual(float(row["ActualPositionAgeMs4"]), 0.0)
            self.assertEqual(row["ActualPositionTimestamp1"], "")
            self.assertEqual(row["ActualPositionAgeMs1"], "")
            samples = load_recording(recording)
            self.assertEqual(len(samples), 1)
            signal_values = samples[0][1]
            self.assertEqual(signal_values[SIGNAL_GROUND_DEVIATION], (0.0,) * 7)
            self.assertEqual(signal_values[SIGNAL_ACTUAL][3], 9.5)

    def test_disabling_actual_position_reader_resets_values_to_zero(self):
        self.simulator.set_read_actual_positions_enabled(True)
        self.simulator.set_read_actual_position(1, 12.5)
        self.simulator.set_read_actual_positions_enabled(False)

        self.assertEqual(self.simulator._read_actual_positions_mm, [0.0, 0.0])
        self.assertEqual(
            self.simulator._read_actual_position_timestamps, [None, None]
        )
        self.assertEqual(
            self.simulator._read_actual_position_monotonic, [None, None]
        )

    def test_recording_without_actual_position_uses_target_position(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            recording = Path(temp_dir) / "legacy.csv"
            with recording.open("w", encoding="utf-8-sig", newline="") as csv_file:
                writer = csv.writer(csv_file, delimiter=";", lineterminator="\n")
                writer.writerow(
                    ["Timestamp"] + [f"Value{axis}" for axis in range(1, 8)]
                )
                writer.writerow(
                    ["2026-08-28 12:00:00.000"] + [32767] * 7
                )

            signal_values = load_recording(recording)[0][1]
            self.assertEqual(signal_values[SIGNAL_ACTUAL], (0.0,) * 7)
            self.assertEqual(
                signal_values[SIGNAL_CALCULATED_ACTUAL],
                signal_values[SIGNAL_TARGET],
            )


if __name__ == "__main__":
    unittest.main()
