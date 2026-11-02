#!/usr/bin/env python3

"""Device-level access to A6 servo drives."""

import time

from .. import ModBus as modbus
from . import a6_registers as reg
from .config import A6_CONFIG
from .logging_config import get_logger

logger = get_logger("a6.driver")


POLL_INTERVAL_S = A6_CONFIG.poll_interval_s
START_OBS_S = A6_CONFIG.homing_start_observation_s
TIMEOUT_S = A6_CONFIG.operation_timeout_s
DI_INVERT_LEVELS = A6_CONFIG.invert_digital_inputs

_DI_LOGIC_ADDR = {
    1: reg.C04_01,
    2: reg.C04_05,
    3: reg.C04_09,
    4: reg.C04_0D,
    5: reg.C04_11,
    6: reg.C04_15,
    7: reg.C04_19,
    8: reg.C04_1D,
}


class A6Driver:
    """A6-specific operations built on top of the Modbus transport."""

    def __init__(self, modbus_module=modbus) -> None:
        self._modbus = modbus_module
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, axis_count: int = 1) -> None:
        self._modbus.connect(axis_count)
        self._connected = True
        for axis in range(1, axis_count + 1):
            self.print_diag(axis, f"{axis}: Connection check axis")

    def disconnect(self) -> None:
        if not self._connected:
            return

        self._connected = False
        first_error = None
        try:
            for axis in range(1, self._modbus.slaves + 1):
                try:
                    self.write_reg(axis, reg.S_ON, 0)
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                    logger.exception(
                        "Axis %s: could not disable servo during shutdown",
                        axis,
                    )
            time.sleep(0.5)
        finally:
            try:
                self._modbus.disconnect()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                else:
                    logger.exception("Modbus disconnect failed after servo shutdown")

        if first_error is not None:
            raise first_error

    def read_regs(self, axis: int, addr: int, qty: int = 1):
        return self._modbus.read_regs(axis, addr, qty)

    def read_i16(self, axis: int, addr: int) -> int:
        return self._modbus.read_i16(axis, addr)

    def read_u32(self, axis: int, addr: int, signed: bool = False) -> int:
        return self._modbus.read_u32(axis, addr, signed)

    def read_f32(self, axis: int, addr: int) -> float:
        return self._modbus.read_f32(axis, addr)

    def write_reg(
        self,
        axis: int,
        addr: int,
        value: int,
        log_hex: bool = False,
        checkCRC: bool = True,
    ) -> None:
        self.write_register(axis, addr, value, log_hex, checkCRC)

    def write_register(
        self, axis_id: int, address: int, value: int,
        log_hex: bool = False, check_crc: bool = True,
    ) -> None:
        self._modbus.write_register(axis_id, address, value, log_hex, check_crc)

    def write_u32(
        self,
        axis: int,
        addr: int,
        value: int,
        log_hex: bool = False,
        checkCRC: bool = True,
    ) -> None:
        self.write_uint32(axis, addr, value, log_hex, checkCRC)

    def write_uint32(
        self, axis_id: int, address: int, value: int,
        log_hex: bool = False, check_crc: bool = True,
    ) -> None:
        self._modbus.write_uint32(axis_id, address, value, log_hex, check_crc)

    def write_two_u32(
        self,
        axis: int,
        addr: int,
        first_value: int,
        second_value: int,
        log_hex: bool = False,
        checkCRC: bool = True,
    ) -> None:
        self._modbus.write_two_u32(
            axis, addr, first_value, second_value, log_hex, checkCRC
        )

    def queue_write_reg(
        self,
        axis: int,
        addr: int,
        value: int,
        log_hex: bool = False,
        checkCRC: bool = True,
    ) -> None:
        self._modbus.queue_write_reg(axis, addr, value, log_hex, checkCRC)

    def queue_write_u32(
        self,
        axis: int,
        addr: int,
        value: int,
        log_hex: bool = False,
        checkCRC: bool = True,
    ) -> None:
        self._modbus.queue_write_u32(axis, addr, value, log_hex, checkCRC)

    def queue_write_two_u32(
        self,
        axis: int,
        addr: int,
        first_value: int,
        second_value: int,
        log_hex: bool = False,
        checkCRC: bool = True,
    ) -> None:
        self._modbus.queue_write_two_u32(
            axis, addr, first_value, second_value, log_hex, checkCRC
        )

    def write_f32(
        self, axis: int, addr: int, value: float, log_hex: bool = False
    ) -> None:
        self._modbus.write_f32(axis, addr, value, log_hex)

    def ensure_word_order(self, axis: int) -> bool:
        return self._modbus._is_high_first(axis)

    def read_axis_current(self, axis: int) -> int:
        return self.read_i16(axis, reg.U40_02)

    def read_axis_motor_current(self, axis: int) -> int:
        return self.read_i16(axis, reg.U40_03)

    def read_axis_load_rate(self, axis: int) -> int:
        return self.read_i16(axis, reg.U40_07)

    def di_active(self, axis: int, di_levels_word: int, di_no: int) -> bool:
        if di_no not in _DI_LOGIC_ADDR:
            raise ValueError(f"Invalid digital input number: {di_no}")
        level = (di_levels_word >> (di_no - 1)) & 1
        if DI_INVERT_LEVELS:
            level ^= 1
        logic = self.read_regs(axis, _DI_LOGIC_ADDR[di_no], 1)[0] & 1
        active_level = 1 if logic == 0 else 0
        return level == active_level

    def print_diag(self, axis: int, prefix: str = "Diag") -> None:
        try:
            di_state = self.read_regs(axis, reg.U40_04, 1)[0]
            do_state = self.read_regs(axis, reg.U40_05, 1)[0]
            status = self.read_regs(axis, reg.U41_0A, 1)[0]
            failure = self.read_regs(axis, reg.U40_43, 1)[0]
            speed = self.read_i16(axis, reg.U40_01)
            current = self.read_axis_current(axis)
            load_rate = self.read_axis_load_rate(axis)
            logger.info(
                "%s: DI=0x%04X DO=0x%04X ServoStatus=%s FailReason=%s "
                "Speed=%srpm Current=%s LoadRate=%s",
                prefix, di_state, do_state, status, failure, speed, current, load_rate,
            )
        except Exception:
            logger.exception("%s: error reading diagnostic values", prefix)

    def homing_done(self, axis: int) -> bool:
        do5_function = self.read_regs(axis, reg.C04_38, 1)[0]
        do5_logic = self.read_regs(axis, reg.C04_39, 1)[0]
        if do5_function != 9:
            logger.warning(
                "DO5 function=%s, expected 9 (Referencing completion)",
                do5_function,
            )
        # U40.05 reports the electrical output level.  With the configured
        # positive logic (C04.39 = 0), an asserted open-collector DO5 is low;
        # negative logic (C04.39 = 1) reverses that level.
        active_level = do5_logic & 0x1
        do_state = self.read_regs(axis, reg.U40_05, 1)[0]
        return ((do_state >> 4) & 0x1) == active_level


driver = A6Driver()


# Compatibility functions keep the current A6Commands API stable.
read_regs = driver.read_regs
read_i16 = driver.read_i16
read_u32 = driver.read_u32
read_f32 = driver.read_f32
write_reg = driver.write_reg
write_u32 = driver.write_u32
write_two_u32 = driver.write_two_u32
write_f32 = driver.write_f32
write_register = driver.write_register
write_uint32 = driver.write_uint32
read_axis_current = driver.read_axis_current
read_axis_motor_current = driver.read_axis_motor_current
read_axis_load_rate = driver.read_axis_load_rate
print_diag = driver.print_diag
homing_done = driver.homing_done
connect = driver.connect
disconnect = driver.disconnect
_is_high_first = driver.ensure_word_order


def di_active(di_levels_word: int, di_no: int, axis: int = 1) -> bool:
    return driver.di_active(axis, di_levels_word, di_no)


def bit(value: int, number: int) -> int:
    return (value >> number) & 1
