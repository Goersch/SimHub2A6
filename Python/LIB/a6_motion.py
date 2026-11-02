#!/usr/bin/env python3

"""Pure A6 motion-unit and spindle-speed conversions."""

from .config import RIG_CONFIG

MAX_AXIS = len(RIG_CONFIG.axes)
SPINDLE_PITCH_MM = tuple(axis.spindle_pitch_mm for axis in RIG_CONFIG.axes)
REFERENCE_SPINDLE_PITCH_MM = RIG_CONFIG.limits.reference_spindle_pitch_mm
REFERENCE_APP_UNITS_PER_MM = RIG_CONFIG.limits.reference_app_units_per_mm
MIN_POSITION_MM = RIG_CONFIG.limits.minimum_mm
MAX_POSITION_MM = RIG_CONFIG.limits.maximum_mm
HOMING_POSITION_MM = RIG_CONFIG.limits.homing_mm

# Compatibility name used by existing code.
spindlePitchMM = SPINDLE_PITCH_MM


def spindle_pitch_mm(axis: int) -> float:
    if not 1 <= axis <= len(SPINDLE_PITCH_MM):
        raise ValueError(f"Invalid axis: {axis}")
    pitch = SPINDLE_PITCH_MM[axis - 1]
    if pitch <= 0:
        raise ValueError(f"Invalid spindle pitch for axis {axis}: {pitch}")
    return pitch


def app_units_per_mm(axis: int) -> float:
    return (
        REFERENCE_APP_UNITS_PER_MM
        * REFERENCE_SPINDLE_PITCH_MM
        / spindle_pitch_mm(axis)
    )


def app_units_from_mm(axis: int, mm_target=0.0) -> int:
    return int(round(mm_target * app_units_per_mm(axis)))


def rpm_from_mm_per_second(axis: int, mm_per_second) -> int:
    return int(mm_per_second * 60.0 / spindle_pitch_mm(axis))


def pitch_rpm_from_rpm(axis: int, rpm) -> int:
    return int(rpm * REFERENCE_SPINDLE_PITCH_MM / spindle_pitch_mm(axis))


def min_position_app_units(axis: int) -> int:
    return app_units_from_mm(axis, MIN_POSITION_MM)


def max_position_app_units(axis: int) -> int:
    return app_units_from_mm(axis, MAX_POSITION_MM)


def homing_position_app_units(axis: int) -> int:
    return app_units_from_mm(axis, HOMING_POSITION_MM)


minPosition = [min_position_app_units(axis) for axis in range(1, MAX_AXIS + 1)]
maxPosition = [max_position_app_units(axis) for axis in range(1, MAX_AXIS + 1)]
homingPosition = [homing_position_app_units(axis) for axis in range(1, MAX_AXIS + 1)]
