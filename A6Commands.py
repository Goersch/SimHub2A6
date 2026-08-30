"""Compatibility API for A6 motion commands.

New code should use :mod:`LIB.a6_motion_controller`.  The historical function
names remain available so existing UI and automation modules keep working.
"""

from __future__ import annotations

import warnings

from .LIB.a6_motion import (
    HOMING_POSITION_MM,
    MAX_AXIS,
    MAX_POSITION_MM,
    MIN_POSITION_MM,
    REFERENCE_APP_UNITS_PER_MM,
    REFERENCE_SPINDLE_PITCH_MM,
    app_units_from_mm,
    app_units_per_mm,
    homing_position_app_units,
    homingPosition,
    max_position_app_units,
    maxPosition,
    min_position_app_units,
    minPosition,
    pitch_rpm_from_rpm,
    rpm_from_mm_per_second,
    spindlePitchMM,
)  # noqa: F401 -- public compatibility re-exports
from .LIB.a6_motion_controller import (  # noqa: F401 -- public compatibility re-exports
    A6MotionController,
    PlannerState,
    motion_controller,
)

maxAxis = MAX_AXIS
planner_state = motion_controller.state


def reset_A6_planner_parameter_cache(slaveId=None):
    return motion_controller.reset_parameter_cache(slaveId)


def reset_A6_planner_position_cache(slaveId=None):
    return motion_controller.reset_position_cache(slaveId)


def A6_initParameters(slaveId):
    return motion_controller.initialize_parameters(slaveId)


def A6_do_homing(slaveId, ignoreStatus=False, init=True, trigger=True):
    return motion_controller.start_homing(
        slaveId,
        ignore_status=ignoreStatus,
        initialize=init,
        trigger=trigger,
    )


def A6_trigger_homing(slaveId):
    return motion_controller.trigger_homing(slaveId)


def A6_check_if_homing_started(slaveId):
    return motion_controller.homing_started(slaveId)


def A6_wait_for_homing_done(slaveId):
    return motion_controller.wait_for_homing(slaveId)


def A6_Homing_done(slaveId, waitForEndPostion=True):
    return motion_controller.homing_complete(
        slaveId, wait_for_end_position=waitForEndPostion
    )


def A6_position_reached(slaveId, ignoreStatus=False):
    return motion_controller.position_reached(slaveId, ignore_status=ignoreStatus)


def A6_read_current(slaveId):
    return motion_controller.read_current(slaveId)


def A6_read_motor_current(slaveId):
    return motion_controller.read_motor_current(slaveId)


def A6_read_load_rate(slaveId):
    return motion_controller.read_load_rate(slaveId)


def A6_read_error_state(slaveId):
    return motion_controller.read_error_state(slaveId)


def A6_read_servo_status(slaveId):
    return motion_controller.read_servo_status(slaveId)


def A6_homing_completed(slaveId):
    return motion_controller.homing_completed(slaveId)


def _deprecated_read_alias(name, replacement):
    warnings.warn(
        f"{name}() is deprecated; use {replacement}()",
        DeprecationWarning,
        stacklevel=2,
    )


def A6_read_strom(slaveId):
    _deprecated_read_alias("A6_read_strom", "A6_read_current")
    return A6_read_current(slaveId)


def A6_read_motorstrom(slaveId):
    _deprecated_read_alias("A6_read_motorstrom", "A6_read_motor_current")
    return A6_read_motor_current(slaveId)


def A6_read_drehmoment(slaveId):
    _deprecated_read_alias("A6_read_drehmoment", "A6_read_load_rate")
    return A6_read_load_rate(slaveId)


def A6_wait_for_position_reached(slaveId):
    return motion_controller.wait_for_position(slaveId)


def A6_move_to_mm(slaveId, mm_target=0.0):
    return motion_controller.move_to_mm(slaveId, mm_target)


def A6_planner_start(slaveId):
    return motion_controller.planner_start(slaveId)


def A6_planner_set_parameters(slaveId, RPM, accel_ms=300, decel_ms=300):
    return motion_controller.planner_set_parameters(
        slaveId, RPM, accel_ms, decel_ms
    )


def A6_planner_set_pos_mm(
    slaveId, mm, log_hex=False, checkCRC=True, trigger=True
):
    return motion_controller.planner_set_position_mm(
        slaveId,
        mm,
        log_hex=log_hex,
        check_crc=checkCRC,
        trigger=trigger,
    )


def A6_planner_trigger(slaveId, log_hex=False, checkCRC=True):
    return motion_controller.planner_trigger(slaveId, log_hex, checkCRC)


def A6_planner_stop(slaveId):
    return motion_controller.planner_stop(slaveId)


def A6_run_random_motion(duration_sec=10):
    return motion_controller.run_random_motion(duration_sec)


def A6_connect(axisCount=1):
    return motion_controller.connect(axisCount)


def A6_disconnect():
    return motion_controller.disconnect()


# Canonical snake_case names for callers not yet using the controller directly.
initialize_parameters = A6_initParameters
start_homing = A6_do_homing
trigger_homing = A6_trigger_homing
homing_completed = A6_Homing_done
position_reached = A6_position_reached
read_current = A6_read_current
read_motor_current = A6_read_motor_current
read_load_rate = A6_read_load_rate
read_error_state = A6_read_error_state
read_servo_status = A6_read_servo_status
move_to_mm = A6_move_to_mm
planner_start = A6_planner_start
planner_set_parameters = A6_planner_set_parameters
planner_set_position_mm = A6_planner_set_pos_mm
planner_trigger = A6_planner_trigger
planner_stop = A6_planner_stop
connect_axes = A6_connect
disconnect_axes = A6_disconnect
