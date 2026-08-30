"""Deprecated compatibility facade for the separated A6 modules."""

import warnings

from .. import ModBus as _modbus
from . import a6_driver as _driver
from . import a6_motion as _motion
from . import a6_registers as _registers

warnings.warn(
    "A6Lib is deprecated; import the a6 modules from LIB directly",
    DeprecationWarning,
    stacklevel=2,
)

_DRIVER_EXPORTS = (
    "POLL_INTERVAL_S", "START_OBS_S", "TIMEOUT_S", "DI_INVERT_LEVELS",
    "A6Driver", "driver", "read_regs", "read_i16", "read_u32", "read_f32",
    "write_reg", "write_u32", "write_two_u32", "write_f32",
    "write_register", "write_uint32",
    "read_axis_current", "read_axis_motor_current", "read_axis_load_rate",
    "print_diag", "homing_done", "connect", "disconnect", "di_active", "bit",
)
_MOTION_EXPORTS = (
    "MAX_AXIS", "SPINDLE_PITCH_MM", "spindlePitchMM",
    "REFERENCE_SPINDLE_PITCH_MM", "REFERENCE_APP_UNITS_PER_MM",
    "MIN_POSITION_MM", "MAX_POSITION_MM", "HOMING_POSITION_MM",
    "spindle_pitch_mm", "app_units_per_mm", "app_units_from_mm",
    "rpm_from_mm_per_second", "pitch_rpm_from_rpm",
    "min_position_app_units", "max_position_app_units",
    "homing_position_app_units", "minPosition", "maxPosition", "homingPosition",
)
_REGISTER_EXPORTS = tuple(
    name
    for name in vars(_registers)
    if name.startswith(("C", "U")) or name in {"S_ON", "POS_TRIG", "HOM_TRIG"}
)

for _name in _DRIVER_EXPORTS:
    globals()[_name] = getattr(_driver, _name)
for _name in _MOTION_EXPORTS:
    globals()[_name] = getattr(_motion, _name)
for _name in _REGISTER_EXPORTS:
    globals()[_name] = getattr(_registers, _name)

_is_high_first = _driver._is_high_first
write_multi = _modbus.write_multi
write_reg_fc10 = _modbus.write_reg_fc10
AU_PER_MM = _motion.REFERENCE_APP_UNITS_PER_MM

__all__ = list(_DRIVER_EXPORTS + _MOTION_EXPORTS + _REGISTER_EXPORTS) + [  # pyright: ignore[reportUnsupportedDunderAll]
    "_is_high_first", "write_multi", "write_reg_fc10", "AU_PER_MM"
]
