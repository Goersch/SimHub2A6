"""High-level motion control for A6 servo drives."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import a6_registers as reg
from .a6_driver import POLL_INTERVAL_S, START_OBS_S, TIMEOUT_S, A6Driver, driver
from .a6_motion import (
    app_units_from_mm,
    app_units_per_mm,
    homing_position_app_units,
    max_position_app_units,
    min_position_app_units,
    pitch_rpm_from_rpm,
)
from .config import A6_CONFIG
from .logging_config import get_logger

logger = get_logger("a6.motion_controller")
_CACHE_MISSING = object()


@dataclass
class PlannerState:
    """Per-axis planner caches guarded for concurrent callers."""

    parameter_cache: dict[int, dict[str, int]] = field(default_factory=dict)
    position_cache: dict[int, int] = field(default_factory=dict)
    parameter_lock: threading.Lock = field(default_factory=threading.Lock)
    position_lock: threading.Lock = field(default_factory=threading.Lock)


class A6MotionController:
    """Coordinates homing, status access and planned A6 movement."""

    def __init__(
        self,
        a6_driver: A6Driver = driver,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.driver = a6_driver
        self.state = PlannerState()
        self._sleep = sleep
        self._clock = clock

    @property
    def connected(self) -> bool:
        return self.driver.connected

    def reset_parameter_cache(self, axis_id: int | None = None) -> None:
        with self.state.parameter_lock:
            if axis_id is None:
                self.state.parameter_cache.clear()
            else:
                self.state.parameter_cache.pop(axis_id, None)

    def reset_position_cache(self, axis_id: int | None = None) -> None:
        with self.state.position_lock:
            if axis_id is None:
                self.state.position_cache.clear()
            else:
                self.state.position_cache.pop(axis_id, None)

    def planner_parameters_match(
        self,
        axis_id: int,
        rpm: int,
        accel_ms: int = 300,
        decel_ms: int = 300,
    ) -> bool:
        """Return whether the requested planner parameters are already active."""
        rpm_value = pitch_rpm_from_rpm(axis_id, max(0, rpm))
        accel_ms = max(100, accel_ms)
        decel_ms = max(100, decel_ms)
        with self.state.parameter_lock:
            cache = self.state.parameter_cache.get(axis_id)
            return bool(
                cache
                and cache.get("rpm") == rpm_value
                and cache.get("accel_ms") == accel_ms
                and cache.get("decel_ms") == decel_ms
            )

    def initialize_parameters(self, axis_id: int) -> None:
        self.driver.write_reg(axis_id, reg.S_ON, 0)
        save = self.driver.read_regs(axis_id, reg.C04_01, 1)[0] != 1
        logger.info("Axis %s: initializing parameters", axis_id)

        self._sleep(0.2)
        self.driver.write_reg(axis_id, reg.C00_22, 1)
        self.driver.write_u32(axis_id, reg.C03_02, 131072)
        self.driver.write_u32(axis_id, reg.C03_04, 131072)
        self.driver.write_reg(axis_id, reg.C04_01, 1)
        self.driver.write_reg(axis_id, reg.C04_05, 1)
        self.driver.write_reg(axis_id, reg.C04_08, 0)
        self.driver.write_reg(axis_id, reg.C04_12, 1)
        self.driver.write_reg(axis_id, reg.C04_14, 19)
        self.driver.write_reg(axis_id, reg.C04_18, 25)
        self.driver.write_reg(axis_id, reg.C04_1C, 0)
        self.driver.write_u32(axis_id, reg.C05_0A, 10)
        self.driver.write_reg(axis_id, reg.C05_0C, 10)
        self.driver.write_u32(axis_id, reg.C0E_00, max_position_app_units(axis_id))
        self.driver.write_u32(axis_id, reg.C0E_03, min_position_app_units(axis_id))
        self.driver.write_reg(axis_id, reg.C10_00, 2)
        self.driver.write_reg(axis_id, reg.C10_01, 17)
        self.driver.write_u32(axis_id, reg.C10_04, 100)
        self.driver.write_u32(axis_id, reg.C10_06, 100)
        self.driver.write_u32(
            axis_id, reg.C10_0B, homing_position_app_units(axis_id)
        )

        if save:
            logger.info("Axis %s: saving new parameters", axis_id)
            self._sleep(0.2)
            self.driver.write_reg(axis_id, reg.C0A_05, 1)
            self._sleep(2.0)
            logger.warning(
                "Axis %s: restart required; check ALF1.0 and press SET", axis_id
            )
        else:
            self.driver.write_reg(axis_id, reg.C0A_05, 0)

    def start_homing(
        self,
        axis_id: int,
        *,
        ignore_status: bool = False,
        initialize: bool = True,
        trigger: bool = True,
    ) -> None:
        if not ignore_status and self.driver.homing_done(axis_id):
            logger.info("Axis %s: homing already completed", axis_id)
            return

        if initialize:
            self.driver.write_reg(axis_id, reg.C10_00, 2)
            self.driver.write_reg(
                axis_id,
                reg.C10_02,
                pitch_rpm_from_rpm(axis_id, A6_CONFIG.homing_initial_rpm),
            )
            self.driver.write_reg(
                axis_id,
                reg.C10_03,
                pitch_rpm_from_rpm(axis_id, A6_CONFIG.homing_end_rpm),
            )
            self.driver.write_u32(axis_id, reg.C10_04, A6_CONFIG.homing_accel_ms)
            self.driver.write_u32(axis_id, reg.C10_06, A6_CONFIG.homing_decel_ms)
            self.driver.write_u32(axis_id, reg.C10_0A, 0)
            self.driver.write_u32(
                axis_id, reg.C10_0B, homing_position_app_units(axis_id)
            )

        self.driver.write_reg(axis_id, reg.S_ON, 1)
        if trigger:
            self.trigger_homing(axis_id)

    def trigger_homing(self, axis_id: int) -> None:
        check_crc = axis_id != 0
        self._sleep(0.25)
        self.driver.write_reg(axis_id, reg.HOM_TRIG, 1, checkCRC=check_crc)
        self._sleep(0.25)
        self.driver.write_reg(axis_id, reg.HOM_TRIG, 0, checkCRC=check_crc)
        logger.info("Axis %s: homing trigger sent", axis_id)

    def homing_started(self, axis_id: int) -> bool:
        started_at = self._clock()
        while self._clock() - started_at < START_OBS_S:
            speed = self.driver.read_regs(axis_id, reg.U40_01, 1)[0]
            status = self.driver.read_regs(axis_id, reg.U41_0A, 1)[0]
            failure = self.driver.read_regs(axis_id, reg.U40_43, 1)[0]
            if abs(speed) > 0 or status == 2:
                logger.info("Axis %s: homing movement detected", axis_id)
                return True
            if failure != 0:
                raise RuntimeError(
                    f"{axis_id}: Homing blocked, Failure reason={failure}"
                )
            self._sleep(0.1)

        logger.warning(
            "Axis %s: no homing movement; check S-ON, limits, inhibit and brake",
            axis_id,
        )
        return False

    def wait_for_homing(self, axis_id: int) -> None:
        started_at = self._clock()
        while not self.homing_complete(axis_id):
            if self._clock() - started_at > TIMEOUT_S:
                raise TimeoutError(f"{axis_id}: Homing-Timeout")
            self._sleep(POLL_INTERVAL_S)
        logger.info("Axis %s: homing completed", axis_id)

    def homing_complete(
        self, axis_id: int, *, wait_for_end_position: bool = True
    ) -> bool:
        speed = self.driver.read_regs(axis_id, reg.U40_01, 1)[0]
        status = self.driver.read_regs(axis_id, reg.U41_0A, 1)[0]
        done = (
            self.driver.homing_done(axis_id)
            and (abs(speed) == 0 or not wait_for_end_position)
            and status in (1, 2)
        )
        if done:
            self.driver.write_reg(axis_id, reg.S_ON, 0)
        return done

    def position_reached(self, axis_id: int, *, ignore_status: bool = False) -> bool:
        status = self.read_servo_status(axis_id)
        speed = self.driver.read_regs(axis_id, reg.U40_01, 1)[0]
        return (status != 2 or ignore_status) and (speed >= 65534 or speed <= 1)

    def read_current(self, axis_id: int) -> int:
        return self.driver.read_axis_current(axis_id)

    def read_motor_current(self, axis_id: int) -> int:
        return self.driver.read_axis_motor_current(axis_id)

    def read_load_rate(self, axis_id: int) -> int:
        return self.driver.read_axis_load_rate(axis_id)

    def read_position_mm(self, axis_id: int) -> float:
        position = self.driver.read_u32(axis_id, reg.U40_16, signed=True)
        return position / app_units_per_mm(axis_id)

    def read_error_state(self, axis_id: int) -> int:
        return self.driver.read_regs(axis_id, reg.U40_43, 1)[0]

    def read_servo_status(self, axis_id: int) -> int:
        return self.driver.read_regs(axis_id, reg.U41_0A, 1)[0]

    def set_servo_enabled(self, axis_id: int, enabled: bool) -> None:
        """Enable the servo (brake released) or disable it (brake applied)."""
        self.driver.write_reg(axis_id, reg.S_ON, 1 if enabled else 0)

    def homing_completed(self, axis_id: int) -> bool:
        return self.driver.homing_done(axis_id)

    def wait_for_position(self, axis_id: int, timeout: float = 5.0) -> bool:
        started_at = self._clock()
        while self._clock() - started_at < timeout:
            if self.position_reached(axis_id):
                return True
            self._sleep(0.1)
        return False

    def _select_internal_positioning(self, axis_id: int) -> None:
        try:
            selected = self.driver.read_regs(axis_id, reg.C03_00, 1)[0]
        except Exception:
            selected = None
        if selected != 1:
            self.driver.write_reg(axis_id, reg.C03_00, 1)

    def _configure_planner(
        self,
        axis_id: int,
        *,
        mode: int,
        position: int,
        rpm: int,
        accel_ms: int,
        decel_ms: int,
    ) -> None:
        self._select_internal_positioning(axis_id)
        self.reset_parameter_cache(axis_id)
        self.driver.write_reg(axis_id, reg.C11_00, mode)
        self.driver.write_reg(axis_id, reg.C11_01, 0)
        self.driver.write_reg(axis_id, reg.C11_02, 1)
        self.driver.write_reg(axis_id, reg.C11_03, 1)
        self.driver.write_reg(axis_id, reg.C11_04, 1)
        self.reset_position_cache(axis_id)
        self.driver.write_u32(axis_id, reg.C11_06, position)
        self.reset_position_cache(axis_id)
        self.driver.write_reg(axis_id, reg.C11_08, pitch_rpm_from_rpm(axis_id, rpm))
        self.driver.write_u32(axis_id, reg.C11_0A, accel_ms)
        self.driver.write_u32(axis_id, reg.C11_0C, decel_ms)
        self.reset_parameter_cache(axis_id)
        self.driver.write_u32(axis_id, reg.C11_0E, 0)

    def move_to_mm(self, axis_id: int, mm_target: float = 0.0) -> bool:
        self.wait_for_position(axis_id)
        self.driver.ensure_word_order(axis_id)
        target = app_units_from_mm(axis_id, mm_target)
        current = self.driver.read_u32(axis_id, reg.U40_16, signed=True)
        logger.info(
            "Axis %s: move to %smm (%s app units, current=%s)",
            axis_id,
            mm_target,
            target,
            current,
        )
        self._configure_planner(
            axis_id,
            mode=0,
            position=target,
            rpm=A6_CONFIG.move_rpm,
            accel_ms=A6_CONFIG.move_accel_ms,
            decel_ms=A6_CONFIG.move_decel_ms,
        )
        self._sleep(0.5)
        self.driver.write_reg(axis_id, reg.S_ON, 1)
        self._sleep(0.3)
        self.driver.write_reg(axis_id, reg.POS_TRIG, 1)
        self._sleep(0.1)

        tolerance = self.driver.read_regs(axis_id, reg.C03_12, 1)[0] or 10
        started_at = self._clock()
        reached = False
        while self._clock() - started_at < TIMEOUT_S:
            position = self.driver.read_u32(axis_id, reg.U40_16, signed=True)
            speed = self.driver.read_regs(axis_id, reg.U40_00, 1)[0]
            if abs(position - target) <= tolerance and abs(speed) < 10:
                logger.info("Axis %s: position reached (%s ~= %s)", axis_id, position, target)
                reached = True
                break
            self._sleep(POLL_INTERVAL_S)

        if not reached:
            logger.error("Axis %s: position target was not reached", axis_id)
        self.driver.write_reg(axis_id, reg.POS_TRIG, 0)
        self.driver.write_reg(axis_id, reg.S_ON, 0)
        return reached

    def planner_start(
        self,
        axis_id: int,
        initial_position_mm: float = 0.0,
    ) -> None:
        # C11 planner parameters are not writable while the drive is enabled.
        # This also makes restarting an already active maintenance planner safe.
        self.driver.write_reg(axis_id, reg.S_ON, 0)
        self._sleep(0.05)
        self._configure_planner(
            axis_id,
            mode=3,
            position=app_units_from_mm(axis_id, initial_position_mm),
            rpm=A6_CONFIG.planner_start_rpm,
            accel_ms=A6_CONFIG.planner_start_accel_ms,
            decel_ms=A6_CONFIG.planner_start_decel_ms,
        )
        self.driver.write_reg(axis_id, reg.S_ON, 1)
        self.driver.write_reg(axis_id, reg.POS_TRIG, 1)
        self._sleep(0.01)
        self.driver.write_reg(axis_id, reg.POS_TRIG, 0)
        self._sleep(0.05)

    def planner_set_parameters(
        self,
        axis_id: int,
        rpm: int,
        accel_ms: int = 300,
        decel_ms: int = 300,
        *,
        queued: bool = False,
    ) -> None:
        rpm = max(0, rpm)
        accel_ms = max(100, accel_ms)
        decel_ms = max(100, decel_ms)
        rpm_value = pitch_rpm_from_rpm(axis_id, rpm)

        with self.state.parameter_lock:
            cache = self.state.parameter_cache.setdefault(axis_id, {})
            write_reg = self.driver.queue_write_reg if queued else self.driver.write_reg
            write_u32 = self.driver.queue_write_u32 if queued else self.driver.write_u32
            write_two_u32 = (
                self.driver.queue_write_two_u32 if queued else self.driver.write_two_u32
            )
            if cache.get("rpm", _CACHE_MISSING) != rpm_value:
                write_reg(axis_id, reg.C11_08, rpm_value)
                cache["rpm"] = rpm_value

            accel_changed = cache.get("accel_ms", _CACHE_MISSING) != accel_ms
            decel_changed = cache.get("decel_ms", _CACHE_MISSING) != decel_ms
            if accel_changed and decel_changed:
                write_two_u32(axis_id, reg.C11_0A, accel_ms, decel_ms)
                cache["accel_ms"] = accel_ms
                cache["decel_ms"] = decel_ms
            elif accel_changed:
                write_u32(axis_id, reg.C11_0A, accel_ms)
                cache["accel_ms"] = accel_ms
            elif decel_changed:
                write_u32(axis_id, reg.C11_0C, decel_ms)
                cache["decel_ms"] = decel_ms

    def planner_set_position_mm(
        self,
        axis_id: int,
        mm: float,
        log_hex: bool = False,
        check_crc: bool = True,
        trigger: bool = True,
        queued: bool = False,
    ) -> None:
        position = app_units_from_mm(axis_id, mm)
        with self.state.position_lock:
            if self.state.position_cache.get(axis_id, _CACHE_MISSING) != position:
                if queued:
                    self.driver.queue_write_u32(
                        axis_id, reg.C11_06, position, log_hex, check_crc
                    )
                else:
                    self.driver.write_u32(
                        axis_id, reg.C11_06, position, log_hex, check_crc
                    )
                self.state.position_cache[axis_id] = position
        if trigger:
            self.planner_trigger(axis_id, log_hex, check_crc)

    def planner_trigger(
        self, axis_id: int, log_hex: bool = False, check_crc: bool = True
    ) -> None:
        if axis_id == 0:
            check_crc = False
        self.driver.write_reg(axis_id, reg.POS_TRIG, 1, log_hex, check_crc)
        self.driver.write_reg(axis_id, reg.POS_TRIG, 0, log_hex, check_crc)

    def planner_stop(self, axis_id: int) -> None:
        self.reset_position_cache(axis_id)
        self.driver.write_u32(axis_id, reg.C11_06, 0)
        self.reset_position_cache(axis_id)
        self.driver.write_reg(axis_id, reg.POS_TRIG, 1)
        self.driver.write_reg(axis_id, reg.POS_TRIG, 0)
        self.driver.write_reg(axis_id, reg.S_ON, 0)
        self.driver.write_reg(axis_id, reg.POS_TRIG, 1)
        self._sleep(0.01)
        self.driver.write_reg(axis_id, reg.POS_TRIG, 0)

    def run_random_motion(self, duration_sec: float = 10) -> None:
        started_at = self._clock()
        while self._clock() - started_at < duration_sec:
            self.planner_set_position_mm(1, random.uniform(-75.0, 75.0))
            self._sleep(random.uniform(0.2, 0.5))

    def connect(self, axis_count: int = 1) -> None:
        self.reset_parameter_cache()
        self.reset_position_cache()
        self.driver.connect(axis_count=axis_count)

    def disconnect(self) -> None:
        try:
            self.driver.disconnect()
        finally:
            self.reset_parameter_cache()
            self.reset_position_cache()


motion_controller = A6MotionController()
