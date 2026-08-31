import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

from SimHub2A6 import Maintenance, SimHubCommands
from SimHub2A6.LIB.a6_motion import app_units_from_mm, rpm_from_mm_per_second
from SimHub2A6.LIB.config import (
    CONFIG_PATH,
    GREASE_CONFIG,
    MODBUS_CONFIG,
    RIG_CONFIG,
    ConfigurationError,
    load_configuration,
)


class ConfigAndMotionTests(unittest.TestCase):
    def test_axis_and_modbus_configuration_are_consistent(self):
        self.assertEqual(len(RIG_CONFIG.axes), 7)
        configured_axes = set().union(
            *(connection.axes for connection in MODBUS_CONFIG.connections)
        )
        enabled_axes = {axis.axis_id for axis in RIG_CONFIG.axes if axis.enabled}
        self.assertEqual(configured_axes, enabled_axes)

    def test_motion_conversions_use_axis_spindle_pitch(self):
        self.assertEqual(app_units_from_mm(1, 10), 10_000)
        self.assertEqual(app_units_from_mm(4, 10), 20_000)
        self.assertEqual(rpm_from_mm_per_second(1, 100), 600)
        self.assertEqual(rpm_from_mm_per_second(4, 100), 1_200)

    def test_hub_motor_geometry_comes_from_rig_configuration(self):
        self.assertEqual(RIG_CONFIG.distance_front_drives_left_to_right_mm, 990.0)
        self.assertEqual(RIG_CONFIG.distance_rear_drives_left_to_right_mm, 990.0)
        self.assertEqual(RIG_CONFIG.distance_front_to_rear_drives_mm, 1660.0)
        self.assertEqual(RIG_CONFIG.center_of_gravity_front_to_rear_mm, 0.0)
        self.assertEqual(RIG_CONFIG.center_of_gravity_left_to_right_mm, 0.0)
        self.assertEqual(RIG_CONFIG.center_of_gravity_height_mm, 900.0)

    def test_actual_position_poll_interval_comes_from_configuration(self):
        from SimHub2A6.LIB.config import CONTROL_CONFIG

        self.assertEqual(CONTROL_CONFIG.actual_position_poll_interval_s, 0.05)

    def test_axes_four_to_seven_share_one_parameter_section(self):
        source = CONFIG_PATH.read_text(encoding="utf-8")
        self.assertIn("[axes_4_7]", source)
        self.assertNotIn("[axis_4]", source)

        grouped_axes = RIG_CONFIG.axes[3:7]
        shared_parameters = {
            (
                axis.enabled,
                axis.spindle_pitch_mm,
                axis.stroke_mm,
                axis.zero_offset_mm,
                axis.speed_mm_s,
                axis.acc_time_ms,
                axis.dec_time_ms,
            )
            for axis in grouped_axes
        }
        self.assertEqual(len(shared_parameters), 1)

    def test_axis_acceleration_times_come_from_ini(self):
        source = CONFIG_PATH.read_text(encoding="utf-8")
        with TemporaryDirectory() as directory:
            test_ini = Path(directory) / "axis_times.ini"
            test_ini.write_text(
                source.replace("accTime = 400", "accTime = 425", 1),
                encoding="utf-8",
            )
            loaded = load_configuration(test_ini)

        self.assertEqual(loaded.rig.axis(1).acc_time_ms, 425)
        self.assertEqual(loaded.rig.axis(1).dec_time_ms, 400)

    def test_simhub_position_uses_axis_acceleration_times(self):
        controller = MagicMock()
        controller.planner_parameters_match.return_value = False
        simulator = MagicMock()
        state = SimHubCommands.SimHubRuntimeState(initialized=True)
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "a6_simulator", simulator),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "ACC_TIME_MS", [321] * 7),
            patch.object(SimHubCommands, "DEC_TIME_MS", [654] * 7),
            patch.object(SimHubCommands, "go_to_pos", return_value=True),
        ):
            updated = SimHubCommands.handle_pos_2(1, 100_000, trigger=False)

        self.assertTrue(updated)
        controller.planner_set_parameters.assert_called_once_with(
            1,
            rpm_from_mm_per_second(1, SimHubCommands.SPEED_MM_S[0]),
            321,
            654,
            queued=True,
        )
        self.assertEqual(
            simulator.set_target.call_args.args[2:5],
            (321, SimHubCommands.SPEED_MM_S[0], 654),
        )

    def test_simhub_position_skips_parameters_when_they_are_already_active(self):
        controller = MagicMock()
        controller.planner_parameters_match.return_value = True
        simulator = MagicMock()
        state = SimHubCommands.SimHubRuntimeState(initialized=True)
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "a6_simulator", simulator),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "go_to_pos", return_value=True),
        ):
            updated = SimHubCommands.handle_pos_2(1, 100_000, trigger=False)

        self.assertTrue(updated)
        controller.planner_set_parameters.assert_not_called()
        simulator.set_target.assert_called_once()

    def test_simhub_positions_are_blocked_during_and_until_after_maintenance(self):
        controller = MagicMock()
        state = SimHubCommands.SimHubRuntimeState(initialized=True)
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "go_to_pos") as go_to_pos,
        ):
            SimHubCommands.set_maintenance_active(True)
            self.assertFalse(SimHubCommands.handle_pos_2(1, 100_000))

            SimHubCommands.set_maintenance_active(False)
            self.assertFalse(SimHubCommands.handle_pos_2(1, 100_000))

            self.assertTrue(SimHubCommands.arm_simhub_positions())
            go_to_pos.return_value = True
            self.assertTrue(SimHubCommands.handle_pos_2(1, 100_000))

        self.assertEqual(go_to_pos.call_count, 1)

    def test_simhub_positions_are_blocked_as_soon_as_shutdown_starts(self):
        controller = MagicMock()
        state = SimHubCommands.SimHubRuntimeState(initialized=False)
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "go_to_pos") as go_to_pos,
        ):
            SimHubCommands.handle_end()

            self.assertTrue(state.shutdown_active)
            self.assertFalse(state.simhub_positions_armed)
            self.assertFalse(SimHubCommands.handle_pos_2(1, 100_000))
            self.assertFalse(SimHubCommands.arm_simhub_positions())

        go_to_pos.assert_not_called()
        controller.disconnect.assert_called_once_with()

    def test_grease_warning_and_alarm_thresholds_come_from_ini(self):
        self.assertEqual(GREASE_CONFIG.warning_after_operating_hours, 80.0)
        self.assertEqual(GREASE_CONFIG.alarm_after_operating_hours, 100.0)

    def test_configuration_is_loaded_from_requested_ini_file(self):
        source = CONFIG_PATH.read_text(encoding="utf-8")
        with TemporaryDirectory() as directory:
            test_ini = Path(directory) / "custom.ini"
            test_ini.write_text(
                source.replace("baud = 115200", "baud = 57600"),
                encoding="utf-8",
            )
            loaded = load_configuration(test_ini)

        self.assertEqual(CONFIG_PATH.name, "SimHub2SimRig.ini")
        self.assertEqual(CONFIG_PATH.parent.name, "INI")
        self.assertEqual(loaded.modbus.baud, 57600)

    def test_duplicate_modbus_axis_is_rejected(self):
        source = CONFIG_PATH.read_text(encoding="utf-8")
        with TemporaryDirectory() as directory:
            test_ini = Path(directory) / "invalid.ini"
            test_ini.write_text(
                source.replace("axes = 5, 7", "axes = 3, 5, 7"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigurationError, "exactly one"):
                load_configuration(test_ini)

    def test_disabled_axes_are_removed_from_active_modbus_connections(self):
        source = CONFIG_PATH.read_text(encoding="utf-8")
        with TemporaryDirectory() as directory:
            test_ini = Path(directory) / "three_axes.ini"
            test_ini.write_text(
                source.replace(
                    "[axes_4_7]\naxes = 4, 5, 6, 7\nenabled = yes",
                    "[axes_4_7]\naxes = 4, 5, 6, 7\nenabled = no",
                ),
                encoding="utf-8",
            )
            loaded = load_configuration(test_ini)

        self.assertEqual(
            [(connection.port, connection.axes) for connection in loaded.modbus.connections],
            [("COM9", frozenset({1, 2, 3}))],
        )

    def test_initialization_homes_only_unreferenced_axes(self):
        controller = MagicMock()
        controller.connected = False
        controller.homing_completed.side_effect = lambda axis: axis != 2
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "wait_for_homing") as wait_for_homing,
            patch.object(SimHubCommands, "axis_to_position"),
            patch.object(SimHubCommands, "wait_for_axis_to_reach_position"),
            patch.object(SimHubCommands, "all_axis_to_position"),
            patch.object(SimHubCommands.Leveling, "load_leveling_offsets", return_value=False),
            patch.object(SimHubCommands.time, "sleep"),
        ):
            SimHubCommands._run_initialization()

        self.assertEqual(
            controller.start_homing.call_args_list,
            [call(2, ignore_status=True, trigger=False)],
        )
        controller.trigger_homing.assert_called_once_with(0)
        self.assertLess(
            controller.mock_calls.index(
                call.start_homing(2, ignore_status=True, trigger=False)
            ),
            controller.mock_calls.index(call.trigger_homing(0)),
        )
        controller.connect.assert_called_once_with(SimHubCommands.axisCount)
        wait_for_homing.assert_called_once_with(1, SimHubCommands.MAX_AXIS)

    def test_maintenance_initialization_is_skipped_when_already_ready(self):
        controller = MagicMock()
        controller.connected = True
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "handle_init") as handle_init,
        ):
            initialized = SimHubCommands.ensure_maintenance_initialized()

        self.assertFalse(initialized)
        handle_init.assert_not_called()

    def test_maintenance_initialization_starts_when_modbus_is_disconnected(self):
        controller = MagicMock()
        controller.connected = False
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "handle_init") as handle_init,
            patch.object(SimHubCommands, "wait_for_homing") as wait_for_homing,
            patch.object(SimHubCommands, "axis_to_position") as axis_to_position,
        ):
            initialized = SimHubCommands.ensure_maintenance_initialized()

        self.assertTrue(initialized)
        controller.connect.assert_called_once_with(SimHubCommands.axisCount)
        self.assertEqual(
            controller.initialize_parameters.call_args_list,
            [call(axis) for axis in range(1, SimHubCommands.axisCount + 1)],
        )
        self.assertEqual(
            controller.planner_stop.call_args_list,
            [call(axis) for axis in range(1, SimHubCommands.axisCount + 1)],
        )
        handle_init.assert_not_called()
        controller.start_homing.assert_not_called()
        wait_for_homing.assert_not_called()
        axis_to_position.assert_not_called()

    def test_maintenance_moves_hub_axes_with_a_shared_target(self):
        controller = MagicMock()
        controller.read_position_mm.side_effect = [10.0, 11.0, 12.0, 13.0]
        state = SimHubCommands.SimHubRuntimeState()
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "axis_to_position") as axis_to_position,
        ):
            SimHubCommands.move_axes_for_maintenance(4, 7, 12345)

        self.assertEqual(
            controller.planner_start.call_args_list,
            [call(4, 10.0), call(5, 11.0), call(6, 12.0), call(7, 13.0)],
        )
        self.assertEqual(
            controller.planner_set_parameters.call_args_list,
            [
                call(axis, 75, 400, 400)
                for axis in range(4, 8)
            ],
        )
        self.assertEqual(
            controller.set_servo_enabled.call_args_list,
            [
                *(call(axis, True) for axis in range(4, 8)),
                *(call(axis, False) for axis in range(4, 8)),
            ],
        )
        axis_to_position.assert_called_once_with(4, 7, 12345)
        self.assertEqual(state.previous_positions[3:7], [10.0, 11.0, 12.0, 13.0])

    def test_hub_maintenance_move_applies_brakes_after_movement_error(self):
        controller = MagicMock()
        controller.read_position_mm.return_value = 0.0
        state = SimHubCommands.SimHubRuntimeState()
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(
                SimHubCommands,
                "axis_to_position",
                side_effect=TimeoutError("target not reached"),
            ),
        ):
            with self.assertRaisesRegex(TimeoutError, "target not reached"):
                SimHubCommands.move_axes_for_maintenance(4, 7, 12345)

        self.assertEqual(
            controller.set_servo_enabled.call_args_list[-4:],
            [call(axis, False) for axis in range(4, 8)],
        )

    def test_repeated_maintenance_move_reuses_running_planner(self):
        controller = MagicMock()
        controller.read_position_mm.side_effect = [12.0, -25.0]
        state = SimHubCommands.SimHubRuntimeState()
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "axis_to_position"),
        ):
            SimHubCommands.move_axes_for_maintenance(2, 2, 0)
            SimHubCommands.move_axes_for_maintenance(2, 2, 131071)

        controller.planner_start.assert_called_once_with(2, 12.0)
        self.assertEqual(
            controller.planner_set_parameters.call_args_list,
            [call(2, 100, 400, 400), call(2, 100, 400, 400)],
        )

    def test_grease_preparation_starts_only_missing_maintenance_planners(self):
        controller = MagicMock()
        controller.read_position_mm.side_effect = [10.0, 30.0]
        state = SimHubCommands.SimHubRuntimeState(
            maintenance_planner_axes={2}
        )
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
        ):
            SimHubCommands.ensure_maintenance_planners([1, 2, 3])

        self.assertEqual(
            controller.planner_start.call_args_list,
            [call(1, 10.0), call(3, 30.0)],
        )
        self.assertEqual(state.maintenance_planner_axes, {1, 2, 3})

    def test_leaving_maintenance_resumes_existing_planners(self):
        controller = MagicMock()
        current_positions = [10.0, -20.0, 30.0, -40.0, 50.0, -60.0, 70.0]
        controller.read_position_mm.side_effect = current_positions
        state = SimHubCommands.SimHubRuntimeState(
            maintenance_planner_axes=set(range(1, 8))
        )
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
        ):
            SimHubCommands.restore_planner_mode_after_maintenance()

        self.assertEqual(
            controller.read_position_mm.call_args_list,
            [call(axis) for axis in range(1, 8)],
        )
        self.assertEqual(
            controller.set_servo_enabled.call_args_list,
            [call(axis, True) for axis in range(1, 8)],
        )
        controller.planner_start.assert_not_called()
        self.assertEqual(state.previous_positions, current_positions)
        self.assertEqual(state.maintenance_planner_axes, set())

    def test_leaving_maintenance_restarts_only_planners_stopped_by_homing(self):
        controller = MagicMock()
        controller.read_position_mm.side_effect = [10.0, -20.0, 30.0]
        state = SimHubCommands.SimHubRuntimeState(
            maintenance_planner_axes={1, 3}
        )
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "enabled_axes", return_value=[1, 2, 3]),
        ):
            SimHubCommands.restore_planner_mode_after_maintenance()

        self.assertEqual(
            controller.set_servo_enabled.call_args_list,
            [call(1, True), call(3, True)],
        )
        controller.planner_start.assert_called_once_with(2, -20.0)
        self.assertEqual(state.previous_positions[:3], [10.0, -20.0, 30.0])
        self.assertEqual(state.maintenance_planner_axes, set())

    def test_maintenance_dialog_restores_planners_before_it_closes(self):
        dialog = MagicMock()
        dialog._closing = False
        dialog._commands = MagicMock()
        dialog._status_after_id = None
        dialog._on_close = MagicMock()
        dialog.winfo_exists.return_value = True

        with patch.object(Maintenance.Grease, "stop_and_wait") as stop_and_wait:
            Maintenance.WartungDialog.close(dialog)

        stop_and_wait.assert_called_once_with()
        dialog._commands.restore_planner_mode_after_maintenance.assert_called_once_with()
        dialog._commands.set_maintenance_active.assert_called_once_with(False)
        dialog.destroy.assert_called_once_with()
        dialog._on_close.assert_called_once_with()

    def test_front_and_rear_maintenance_moves_use_100_rpm(self):
        controller = MagicMock()
        controller.read_position_mm.return_value = 0.0
        state = SimHubCommands.SimHubRuntimeState()
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "axis_to_position"),
        ):
            SimHubCommands.move_axes_for_maintenance(1, 1, 0)
            SimHubCommands.move_axes_for_maintenance(3, 3, 0)

        self.assertEqual(
            controller.planner_set_parameters.call_args_list,
            [call(1, 100, 400, 400), call(3, 100, 400, 400)],
        )

    def test_maintenance_rejects_movement_when_an_axis_is_not_homed(self):
        controller = MagicMock()
        controller.homing_completed.side_effect = lambda axis: axis != 6
        with patch.object(SimHubCommands, "motion_controller", controller):
            with self.assertRaisesRegex(RuntimeError, r"not homed: \[6\]"):
                SimHubCommands.move_axes_for_maintenance(4, 7, 12345)

        controller.planner_start.assert_not_called()

    def test_maintenance_homes_all_hub_axes(self):
        controller = MagicMock()
        state = SimHubCommands.SimHubRuntimeState()
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "wait_for_homing") as wait_for_homing,
        ):
            SimHubCommands.home_hub_axes_for_maintenance()

        self.assertEqual(
            controller.planner_stop.call_args_list,
            [call(4), call(5), call(6), call(7)],
        )
        self.assertEqual(
            controller.start_homing.call_args_list,
            [
                call(4, ignore_status=True, trigger=False),
                call(5, ignore_status=True, trigger=False),
                call(6, ignore_status=True, trigger=False),
                call(7, ignore_status=True, trigger=False),
            ],
        )
        controller.trigger_homing.assert_called_once_with(0)
        trigger_index = controller.mock_calls.index(call.trigger_homing(0))
        for axis in range(4, 8):
            self.assertLess(
                controller.mock_calls.index(
                    call.start_homing(axis, ignore_status=True, trigger=False)
                ),
                trigger_index,
            )
        wait_for_homing.assert_called_once_with(4, 7)
        self.assertEqual(
            state.previous_positions[3:7],
            [RIG_CONFIG.limits.homing_mm] * 4,
        )

    def test_maintenance_homing_status_checks_all_groups(self):
        controller = MagicMock()
        controller.homing_completed.side_effect = lambda axis: axis != 6
        with patch.object(SimHubCommands, "motion_controller", controller):
            status = SimHubCommands.maintenance_homing_status()

        self.assertEqual(status, (True, True, True, False))

    def test_maintenance_homes_front_and_rear_axes_individually(self):
        with patch.object(SimHubCommands, "home_axes_for_maintenance") as home_axes:
            SimHubCommands.home_front_axis_for_maintenance()
            SimHubCommands.home_rear_axis_for_maintenance()

        self.assertEqual(home_axes.call_args_list, [call(1, 1), call(3, 3)])

    def test_maintenance_homes_middle_axis_only(self):
        controller = MagicMock()
        state = SimHubCommands.SimHubRuntimeState()
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "wait_for_homing") as wait_for_homing,
        ):
            SimHubCommands.home_middle_axis_for_maintenance()

        controller.planner_stop.assert_called_once_with(2)
        controller.start_homing.assert_called_once_with(
            2,
            ignore_status=True,
            trigger=True,
        )
        controller.trigger_homing.assert_not_called()
        wait_for_homing.assert_called_once_with(2, 2)
        self.assertEqual(state.previous_positions[1], RIG_CONFIG.limits.homing_mm)

    def test_shutdown_disconnects_when_safe_positioning_fails(self):
        controller = MagicMock()
        state = SimHubCommands.SimHubRuntimeState(initialized=True)
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(
                SimHubCommands,
                "go_to_pos",
                side_effect=RuntimeError("positioning failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "positioning failed"):
                SimHubCommands.handle_end()

        controller.disconnect.assert_called_once_with()
        self.assertFalse(state.initialized)

    def test_shutdown_centers_all_axes_before_stopping_planners(self):
        controller = MagicMock()
        state = SimHubCommands.SimHubRuntimeState(initialized=True)
        workflow = MagicMock()
        controller.planner_stop.side_effect = workflow.planner_stop
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(
                SimHubCommands.time,
                "sleep",
                side_effect=workflow.sleep,
            ),
            patch.object(
                SimHubCommands,
                "center_all_axes",
                side_effect=workflow.center_all_axes,
            ),
            patch.object(SimHubCommands, "wait_for_homing"),
        ):
            SimHubCommands.handle_end()

        self.assertEqual(workflow.mock_calls[0], call.center_all_axes())
        self.assertEqual(
            workflow.mock_calls[1],
            call.sleep(SimHubCommands.SHUTDOWN_CENTER_SETTLE_S),
        )
        self.assertEqual(
            workflow.mock_calls[2:9],
            [call.planner_stop(axis) for axis in range(1, 8)],
        )
        self.assertFalse(state.initialized)
        controller.disconnect.assert_called_once_with()

    def test_shutdown_prepares_hub_axes_before_shared_homing_trigger(self):
        controller = MagicMock()
        state = SimHubCommands.SimHubRuntimeState(initialized=True)
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands.time, "sleep") as sleep,
            patch.object(SimHubCommands, "center_all_axes"),
            patch.object(SimHubCommands, "wait_for_homing") as wait_for_homing,
        ):
            SimHubCommands.handle_end()

        self.assertEqual(
            controller.start_homing.call_args_list,
            [
                call(
                    axis,
                    ignore_status=True,
                    initialize=False,
                    trigger=False,
                )
                for axis in range(4, 8)
            ],
        )
        sleep.assert_called_once_with(SimHubCommands.SHUTDOWN_CENTER_SETTLE_S)
        controller.trigger_homing.assert_called_once_with(0)
        wait_for_homing.assert_called_once_with(4, 7)

    def test_shutdown_skips_motion_when_initialization_failed(self):
        controller = MagicMock()
        state = SimHubCommands.SimHubRuntimeState(initialized=False)
        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "go_to_pos") as go_to_pos,
        ):
            SimHubCommands.handle_end()

        go_to_pos.assert_not_called()
        controller.disconnect.assert_called_once_with()

    def test_hub_fault_centers_hub_axes_only_once_when_blocking_starts(self):
        controller = MagicMock()
        controller.read_servo_status.return_value = 3
        state = SimHubCommands.SimHubRuntimeState()

        with (
            patch.object(SimHubCommands, "motion_controller", controller),
            patch.object(SimHubCommands, "runtime_state", state),
            patch.object(SimHubCommands, "enabled_axes", return_value=[4, 5, 6, 7]),
            patch.object(
                SimHubCommands, "_center_hub_axes_after_fault"
            ) as center_hub_axes,
            patch.object(SimHubCommands.time, "monotonic", side_effect=[1.0, 1.2]),
        ):
            self.assertFalse(SimHubCommands._hub_axes_accept_simhub_positions())
            self.assertFalse(SimHubCommands._hub_axes_accept_simhub_positions())

        center_hub_axes.assert_called_once_with([4, 5, 6, 7])
