#!/usr/bin/env python3

"""Modbus-RTU communication used by the A6 servo drives."""

import queue
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import serial

from .LIB.config import MODBUS_CONFIG
from .LIB.logging_config import get_logger

logger = get_logger("modbus")


# Serial/Modbus settings. Each tuple contains the port and its Modbus slave IDs.
CONNECTION_CONFIGS = (
    *((connection.port, connection.axes) for connection in MODBUS_CONFIG.connections),
)
BAUD = MODBUS_CONFIG.baud
SERIAL_TIMEOUT_S = MODBUS_CONFIG.serial_timeout_s
MODBUS_INTER_FRAME_DELAY_S = MODBUS_CONFIG.inter_frame_delay_s
SERIAL_RESPONSE_POLL_DELAY_S = MODBUS_CONFIG.response_poll_delay_s
QUEUE_DRAIN_TIMEOUT_S = MODBUS_CONFIG.queue_drain_timeout_s
TASK_WAIT_TIMEOUT_S = MODBUS_CONFIG.task_wait_timeout_s
WORKER_JOIN_TIMEOUT_S = MODBUS_CONFIG.worker_join_timeout_s

# Function and device-specific error codes
FC_READ = 0x03
FC_WRITE = 0x06
FC_WRITE_MULTI = 0x10
FC_ERROR = 0x90

# A6 register controlling the word order of 32-bit values.
WORD_ORDER_REGISTER = 0x0A06


class ModbusTimeoutError(TimeoutError):
    """A queued Modbus operation did not complete within its time budget."""


class ModbusCrcError(RuntimeError):
    """A Modbus response was received with an invalid CRC."""


# Register writes are idempotent.  A little more tolerance here is useful on
# the RS-485 bus because a single noisy response must not abort a complete
# planner setup (as used by the maintenance controls).
WRITE_CRC_RETRIES = 3
WRITE_CRC_RETRY_DELAY_S = 0.01

@dataclass
class _TransferTask:
    frame: bytes
    expect_len: int | None
    timeout: float
    log_hex: bool
    wait_for_response: bool
    response_validator: Callable[[bytes], None] | None = None
    done: threading.Event = field(default_factory=threading.Event)
    response: bytes = b""
    error: BaseException | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)


class _ModbusConnection:
    def __init__(
        self,
        port: str,
        axes,
        serial_port,
        inter_frame_delay: float,
        response_poll_delay: float,
    ) -> None:
        self.port = port
        self.axes = axes
        self.serial = serial_port
        self.inter_frame_delay = inter_frame_delay
        self.response_poll_delay = response_poll_delay
        self.input_queue = queue.Queue()
        self.next_tx_at = 0.0
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self.thread = threading.Thread(
            target=self._send_loop,
            name=f"Modbus-{port}",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def submit(self, task: _TransferTask) -> None:
        with self._pending_lock:
            self._pending_count += 1
            self._idle.clear()
        self.input_queue.put(task)

    def wait_until_idle(self, timeout: float) -> bool:
        return self._idle.wait(max(0.0, timeout))

    def _send_loop(self) -> None:
        while True:
            task = self.input_queue.get()
            try:
                if task is _STOP_TASK:
                    return
                try:
                    if not task.cancelled.is_set():
                        task.response = self._perform_transfer(task)
                        if task.response_validator is not None:
                            task.response_validator(task.response)
                except BaseException as exc:
                    task.error = exc
                finally:
                    task.done.set()
            finally:
                self.input_queue.task_done()
                if task is not _STOP_TASK:
                    with self._pending_lock:
                        self._pending_count -= 1
                        if self._pending_count == 0:
                            self._idle.set()

    def _perform_transfer(self, task: _TransferTask) -> bytes:
        remaining_delay = self.next_tx_at - time.monotonic()
        if remaining_delay > 0:
            time.sleep(remaining_delay)

        self.serial.reset_input_buffer()
        if task.log_hex:
            _log_tx(task.frame)
        self.serial.write(task.frame)
        self.serial.flush()

        buf = bytearray()
        if task.wait_for_response:
            started_at = time.monotonic()
            while time.monotonic() - started_at < task.timeout:
                if self.serial.in_waiting:
                    buf.extend(self.serial.read(self.serial.in_waiting))
                    if task.expect_len and len(buf) >= task.expect_len:
                        break
                else:
                    time.sleep(self.response_poll_delay)

        self.next_tx_at = time.monotonic() + self.inter_frame_delay
        return bytes(buf)


_STOP_TASK = object()


class ModbusManager:
    """Owns all Modbus connections and serializes access to each bus."""

    def __init__(
        self,
        connection_configs=CONNECTION_CONFIGS,
        baud: int = BAUD,
        serial_timeout: float = SERIAL_TIMEOUT_S,
        inter_frame_delay: float = MODBUS_INTER_FRAME_DELAY_S,
        response_poll_delay: float = SERIAL_RESPONSE_POLL_DELAY_S,
        queue_drain_timeout: float = QUEUE_DRAIN_TIMEOUT_S,
        task_wait_timeout: float = TASK_WAIT_TIMEOUT_S,
        worker_join_timeout: float = WORKER_JOIN_TIMEOUT_S,
        serial_factory=None,
    ) -> None:
        self.connection_configs = tuple(connection_configs)
        self.baud = baud
        self.serial_timeout = serial_timeout
        self.inter_frame_delay = inter_frame_delay
        self.response_poll_delay = response_poll_delay
        self.queue_drain_timeout = queue_drain_timeout
        self.task_wait_timeout = task_wait_timeout
        self.worker_join_timeout = worker_join_timeout
        self.serial_factory = serial_factory
        self.slaves = 1
        self._submission_gate = threading.RLock()
        self._word_order_lock = threading.Lock()
        self._words_high_first = None
        self._connections_by_port = {}
        self._connections_by_axis = {}
        self._queued_tasks = []

    @property
    def connected(self) -> bool:
        return bool(self._connections_by_port)

    @property
    def primary_serial(self):
        if not self._connections_by_port:
            return None
        return next(iter(self._connections_by_port.values())).serial

    def connect(self, axis_count: int = 1) -> None:
        with self._submission_gate:
            if self.connected:
                raise RuntimeError("Modbus is already connected")

            self._validate_configuration()
            self.slaves = axis_count
            self._words_high_first = None
            self._queued_tasks.clear()
            serial_factory = self.serial_factory or serial.Serial
            opened_connections = []
            try:
                for port, axes in self.connection_configs:
                    serial_port = serial_factory(
                        port,
                        self.baud,
                        bytesize=8,
                        parity=serial.PARITY_NONE,
                        stopbits=1,
                        timeout=self.serial_timeout,
                    )
                    opened_connections.append(
                        _ModbusConnection(
                            port,
                            axes,
                            serial_port,
                            self.inter_frame_delay,
                            self.response_poll_delay,
                        )
                    )
            except BaseException:
                for connection in opened_connections:
                    connection.serial.close()
                raise

            for connection in opened_connections:
                self._connections_by_port[connection.port] = connection
                for axis in connection.axes:
                    self._connections_by_axis[axis] = connection

            try:
                for connection in opened_connections:
                    connection.start()
            except BaseException:
                self._close_connections(opened_connections)
                self._connections_by_axis.clear()
                self._connections_by_port.clear()
                raise

    def disconnect(self) -> None:
        with self._submission_gate:
            connections = list(self._connections_by_port.values())
            drain_error = None
            deadline = time.monotonic() + self.queue_drain_timeout
            try:
                self._wait_for_idle_connections(connections, deadline)
            except ModbusTimeoutError as exc:
                drain_error = exc

            queued_error = None
            if drain_error is None:
                try:
                    self._raise_queued_errors()
                except BaseException as exc:
                    queued_error = exc

            close_error = None
            try:
                self._close_connections(connections, force=drain_error is not None)
            except ModbusTimeoutError as exc:
                close_error = exc
            finally:
                self._connections_by_axis.clear()
                self._connections_by_port.clear()
                self._words_high_first = None

            if drain_error is not None:
                raise drain_error
            if close_error is not None:
                raise close_error
            if queued_error is not None:
                raise queued_error

    def xfer(
        self,
        frame: bytes,
        expect_len=None,
        timeout: float = 1.0,
        log_hex: bool = True,
        wait_for_response: bool = True,
    ) -> bytes:
        if not frame:
            raise ValueError("A Modbus frame must not be empty")

        slave_id = frame[0]
        if slave_id == 0:
            return self._broadcast_xfer(
                frame, expect_len, timeout, log_hex, wait_for_response
            )

        with self._submission_gate:
            connection = self._connections_by_axis.get(slave_id)
            if connection is None:
                raise RuntimeError(f"No Modbus connection configured for axis {slave_id}")
            task = _TransferTask(frame, expect_len, timeout, log_hex, wait_for_response)
            connection.submit(task)

        return self._wait_for_task(task, timeout)

    def queue_xfer(
        self,
        frame: bytes,
        expect_len=None,
        timeout: float = 1.0,
        log_hex: bool = True,
        wait_for_response: bool = True,
        response_validator: Callable[[bytes], None] | None = None,
    ) -> None:
        """Queue a unicast transfer without waiting in the submitting thread."""

        if not frame:
            raise ValueError("A Modbus frame must not be empty")
        slave_id = frame[0]
        if slave_id == 0:
            raise ValueError("Broadcast transfers cannot be queued asynchronously")

        with self._submission_gate:
            connection = self._connections_by_axis.get(slave_id)
            if connection is None:
                raise RuntimeError(f"No Modbus connection configured for axis {slave_id}")
            task = _TransferTask(
                frame,
                expect_len,
                timeout,
                log_hex,
                wait_for_response,
                response_validator,
            )
            self._queued_tasks.append(task)
            connection.submit(task)

    def get_word_order(self, slave_id: int, register_reader) -> bool:
        with self._word_order_lock:
            if self._words_high_first is None:
                probe_axis = 1 if slave_id == 0 else slave_id
                value = register_reader(probe_axis, WORD_ORDER_REGISTER, 1)[0] & 0x1
                self._words_high_first = value == 1
            return self._words_high_first

    def _broadcast_xfer(
        self,
        frame: bytes,
        expect_len,
        timeout: float,
        log_hex: bool,
        wait_for_response: bool,
    ) -> bytes:
        # Holding the gate prevents new normal submissions while all queues and
        # currently executing transfers drain before the broadcast is queued.
        with self._submission_gate:
            connections = list(self._connections_by_port.values())
            if not connections:
                raise RuntimeError("Modbus is not connected")
            drain_deadline = time.monotonic() + self.queue_drain_timeout
            self._wait_for_idle_connections(connections, drain_deadline)
            self._raise_queued_errors()

            tasks = []
            for connection in connections:
                task = _TransferTask(
                    frame, expect_len, timeout, log_hex, wait_for_response
                )
                tasks.append(task)
                connection.submit(task)

            responses = []
            errors = []
            response_budget = (
                timeout + self.serial_timeout + 1.0 if wait_for_response else 0.0
            )
            task_deadline = time.monotonic() + max(
                self.task_wait_timeout,
                response_budget,
            )
            for task in tasks:
                remaining = task_deadline - time.monotonic()
                if not task.done.wait(max(0.0, remaining)):
                    for pending_task in tasks:
                        pending_task.cancelled.set()
                    raise ModbusTimeoutError(
                        "Modbus broadcast did not complete on all connections"
                    )
                if task.error is not None:
                    errors.append(task.error)
                responses.append(task.response)

            if errors:
                raise errors[0]
            return responses[0] if responses else b""

    def _raise_queued_errors(self) -> None:
        tasks = self._queued_tasks
        self._queued_tasks = []
        for task in tasks:
            if not task.done.is_set():
                raise RuntimeError("Queued Modbus task was not completed after queue drain")
            if task.error is not None:
                raise task.error

    def _wait_for_task(self, task: _TransferTask, transfer_timeout: float) -> bytes:
        response_budget = (
            transfer_timeout + self.serial_timeout + 1.0
            if task.wait_for_response
            else 0.0
        )
        wait_timeout = max(
            self.task_wait_timeout,
            response_budget,
        )
        if not task.done.wait(wait_timeout):
            task.cancelled.set()
            raise ModbusTimeoutError(
                f"Modbus task did not complete within {wait_timeout:.1f}s"
            )
        if task.error is not None:
            raise task.error
        return task.response

    @staticmethod
    def _wait_for_idle_connections(connections, deadline: float) -> None:
        for connection in connections:
            remaining = deadline - time.monotonic()
            if not connection.wait_until_idle(remaining):
                raise ModbusTimeoutError(
                    f"Modbus queue for {connection.port} did not become idle"
                )

    def _validate_configuration(self) -> None:
        ports = set()
        axes = set()
        for port, configured_axes in self.connection_configs:
            if port in ports:
                raise ValueError(f"Modbus port {port} is configured more than once")
            ports.add(port)
            overlap = axes.intersection(configured_axes)
            if overlap:
                duplicate = min(overlap)
                raise ValueError(f"Axis {duplicate} is configured more than once")
            axes.update(configured_axes)

    def _close_connections(self, connections, force: bool = False) -> None:
        close_errors = []
        if force:
            for connection in connections:
                try:
                    connection.serial.close()
                except BaseException as exc:
                    close_errors.append(exc)
        for connection in connections:
            if connection.thread.is_alive():
                connection.input_queue.put(_STOP_TASK)
        stuck_ports = []
        for connection in connections:
            if connection.thread.is_alive():
                connection.thread.join(self.worker_join_timeout)
            if not force:
                try:
                    connection.serial.close()
                except BaseException as exc:
                    close_errors.append(exc)
            if connection.thread.is_alive():
                stuck_ports.append(connection.port)
        if stuck_ports:
            raise ModbusTimeoutError(
                f"Modbus worker threads did not stop: {', '.join(stuck_ports)}"
            )
        if close_errors:
            raise close_errors[0]


manager = ModbusManager()
ser = None  # Compatibility aliases for existing callers.
slaves = 1


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def build_read(slave_id: int, addr: int, qty: int = 1) -> bytes:
    pdu = struct.pack(">BBHH", slave_id, FC_READ, addr, qty)
    return pdu + struct.pack("<H", crc16_modbus(pdu))


def build_write(slave_id: int, addr: int, value: int) -> bytes:
    pdu = struct.pack(">BBHH", slave_id, FC_WRITE, addr, value & 0xFFFF)
    return pdu + struct.pack("<H", crc16_modbus(pdu))


def build_write_multi(slave_id: int, addr: int, values) -> bytes:
    """Build an FC 0x10 request for a sequence of 16-bit values."""
    count = len(values)
    pdu = struct.pack(">BBHHB", slave_id, FC_WRITE_MULTI, addr, count, count * 2)
    pdu += b"".join(struct.pack(">H", value & 0xFFFF) for value in values)
    return pdu + struct.pack("<H", crc16_modbus(pdu))


def _log_tx(frame: bytes) -> None:
    logger.debug("TX %s", " ".join(f"{byte:02X}" for byte in frame))


def xfer(
    frame: bytes,
    expect_len=None,
    timeout: float = 1.0,
    log_hex: bool = True,
    wait_for_response: bool = True,
) -> bytes:
    return manager.xfer(frame, expect_len, timeout, log_hex, wait_for_response)


def queue_xfer(
    frame: bytes,
    expect_len=None,
    timeout: float = 1.0,
    log_hex: bool = True,
    wait_for_response: bool = True,
    response_validator: Callable[[bytes], None] | None = None,
) -> None:
    manager.queue_xfer(
        frame,
        expect_len,
        timeout,
        log_hex,
        wait_for_response,
        response_validator,
    )


def _check_crc(rx: bytes, where: str) -> None:
    if len(rx) < 3:
        raise RuntimeError(f"{where}: Response too short")
    calculated = crc16_modbus(rx[:-2])
    received = struct.unpack("<H", rx[-2:])[0]
    if calculated != received:
        raise ModbusCrcError(
            f"{where}: CRC error (calc=0x{calculated:04X}, got=0x{received:04X})"
        )


def _parse_error(rx: bytes, where: str) -> None:
    if len(rx) >= 8 and rx[1] == FC_ERROR:
        code = (rx[2] << 24) | (rx[3] << 16) | (rx[4] << 8) | rx[5]
        raise RuntimeError(f"{where}: Drive-Error 0x{code:08X}")


def read_regs(slave_id: int, addr: int, qty: int = 1):
    tx = build_read(slave_id, addr, qty)
    expected_length = 1 + 1 + 1 + qty * 2 + 2
    rx = xfer(
        tx,
        expect_len=expected_length,
        timeout=1.0 + 0.01 * qty,
        log_hex=False,
    )
    if not rx:
        raise RuntimeError(f"Read error @0x{addr:04X}: No response")
    _check_crc(rx, f"Read @0x{addr:04X}")
    _parse_error(rx, f"Read @0x{addr:04X}")
    if rx[0] != slave_id or rx[1] != FC_READ:
        raise RuntimeError(f"Read @0x{addr:04X}: Unexpected response (Addr/Fc)")
    byte_count = rx[2]
    if byte_count < qty * 2 or len(rx) < 3 + qty * 2 + 2:
        raise RuntimeError(f"Read @0x{addr:04X}: Bytecount does not match")
    data = rx[3 : 3 + qty * 2]
    return [struct.unpack(">H", data[i : i + 2])[0] for i in range(0, qty * 2, 2)]


def read_i16(slave_id: int, addr: int) -> int:
    value = read_regs(slave_id, addr, 1)[0]
    return value - 0x10000 if value & 0x8000 else value


def write_reg(
    slave_id: int,
    addr: int,
    value: int,
    log_hex: bool = False,
    checkCRC: bool = True,
) -> None:
    tx = build_write(slave_id, addr, value)
    wait_for_response = checkCRC and slave_id != 0
    for attempt in range(WRITE_CRC_RETRIES + 1):
        rx = xfer(
            tx,
            expect_len=8,
            timeout=0.2,
            log_hex=log_hex,
            wait_for_response=wait_for_response,
        )
        if not wait_for_response:
            return
        try:
            _validate_write_reg_response(rx, slave_id, addr)
            return
        except ModbusCrcError as exc:
            if attempt >= WRITE_CRC_RETRIES:
                raise
            logger.warning(
                "Write CRC error @0x%04X (axis=%s, attempt=%s/%s, rx=%s): %s; "
                "retrying",
                addr,
                slave_id,
                attempt + 1,
                WRITE_CRC_RETRIES + 1,
                rx.hex(" ") or "<empty>",
                exc,
            )
            time.sleep(WRITE_CRC_RETRY_DELAY_S * (attempt + 1))


def _validate_write_reg_response(rx: bytes, slave_id: int, addr: int) -> None:
    if not rx:
        logger.error("Write error @0x%04X: no response (axis=%s)", addr, slave_id)
        return
    _check_crc(rx, f"Write @0x{addr:04X}")
    _parse_error(rx, f"Write @0x{addr:04X}")
    if len(rx) < 8 or rx[0] != slave_id or rx[1] != FC_WRITE:
        raise RuntimeError(
            f"Write @0x{addr:04X}: Unexpected response (Addr/Fc): {rx.hex()}"
        )


def queue_write_reg(
    slave_id: int,
    addr: int,
    value: int,
    log_hex: bool = False,
    checkCRC: bool = True,
) -> None:
    if slave_id == 0:
        raise ValueError("Use write_reg for broadcasts")
    tx = build_write(slave_id, addr, value)
    def validator(rx):
        return (_validate_write_reg_response(rx, slave_id, addr)
            if checkCRC
            else None)
    queue_xfer(
        tx,
        expect_len=8,
        timeout=0.2,
        log_hex=log_hex,
        wait_for_response=checkCRC,
        response_validator=validator,
    )


def _is_high_first(slave_id: int) -> bool:
    return manager.get_word_order(slave_id, read_regs)


def read_u32(slave_id: int, addr: int, signed: bool = False) -> int:
    word0, word1 = read_regs(slave_id, addr, 2)
    high, low = (word0, word1) if _is_high_first(slave_id) else (word1, word0)
    value = (high << 16) | low
    if signed and value & 0x80000000:
        value -= 0x100000000
    return value


def read_f32(slave_id: int, addr: int) -> float:
    word0, word1 = read_regs(slave_id, addr, 2)
    high, low = (word0, word1) if _is_high_first(slave_id) else (word1, word0)
    return struct.unpack(">f", struct.pack(">HH", high, low))[0]


def _u32_register_values(slave_id: int, value: int):
    if value < 0:
        value = (value + (1 << 32)) & 0xFFFFFFFF
    high = (value >> 16) & 0xFFFF
    low = value & 0xFFFF
    return [high, low] if _is_high_first(slave_id) else [low, high]


def write_multi(
    slave_id: int,
    addr: int,
    values_u16,
    log_hex: bool = True,
    checkCRC: bool = True,
) -> None:
    tx = build_write_multi(slave_id, addr, values_u16)
    wait_for_response = checkCRC and slave_id != 0
    rx = xfer(tx, expect_len=8, log_hex=log_hex, wait_for_response=wait_for_response)
    if wait_for_response:
        _validate_write_multi_response(rx, slave_id, addr)


def _validate_write_multi_response(rx: bytes, slave_id: int, addr: int) -> None:
    if not rx or len(rx) < 5:
        logger.error(
            "Write(FC10) @0x%04X: no answer / too short (axis=%s)",
            addr,
            slave_id,
        )
        return
    _check_crc(rx, f"Write(FC10) @0x{addr:04X}")
    if rx[1] & 0x80:
        raise RuntimeError(
            f"Write(FC10) @0x{addr:04X}: Modbus Exception 0x{rx[2]:02X}"
        )
    if rx[0] != slave_id or rx[1] != FC_WRITE_MULTI:
        raise RuntimeError(f"Write(FC10) @0x{addr:04X}: Unexpected response")


def queue_write_multi(
    slave_id: int,
    addr: int,
    values_u16,
    log_hex: bool = True,
    checkCRC: bool = True,
) -> None:
    if slave_id == 0:
        raise ValueError("Use write_multi for broadcasts")
    tx = build_write_multi(slave_id, addr, values_u16)
    def validator(rx):
        return (_validate_write_multi_response(rx, slave_id, addr)
            if checkCRC
            else None)
    queue_xfer(
        tx,
        expect_len=8,
        log_hex=log_hex,
        wait_for_response=checkCRC,
        response_validator=validator,
    )


def write_f32(slave_id: int, addr: int, value: float, log_hex: bool = False) -> None:
    raw = struct.pack(">f", float(value))
    high = struct.unpack(">H", raw[0:2])[0]
    low = struct.unpack(">H", raw[2:4])[0]
    values = [high, low] if _is_high_first(slave_id) else [low, high]
    write_multi(slave_id, addr, values, log_hex)


def write_u32(
    slave_id: int,
    addr: int,
    value: int,
    log_hex: bool = False,
    checkCRC: bool = True,
) -> None:
    write_multi(
        slave_id,
        addr,
        _u32_register_values(slave_id, value),
        log_hex,
        checkCRC,
    )


def write_two_u32(
    slave_id: int,
    addr: int,
    first_value: int,
    second_value: int,
    log_hex: bool = False,
    checkCRC: bool = True,
) -> None:
    values = _u32_register_values(slave_id, first_value)
    values.extend(_u32_register_values(slave_id, second_value))
    write_multi(slave_id, addr, values, log_hex, checkCRC)


def queue_write_u32(
    slave_id: int,
    addr: int,
    value: int,
    log_hex: bool = False,
    checkCRC: bool = True,
) -> None:
    queue_write_multi(
        slave_id,
        addr,
        _u32_register_values(slave_id, value),
        log_hex,
        checkCRC,
    )


def queue_write_two_u32(
    slave_id: int,
    addr: int,
    first_value: int,
    second_value: int,
    log_hex: bool = False,
    checkCRC: bool = True,
) -> None:
    values = _u32_register_values(slave_id, first_value)
    values.extend(_u32_register_values(slave_id, second_value))
    queue_write_multi(slave_id, addr, values, log_hex, checkCRC)


def write_reg_fc10(slave_id: int, addr: int, value: int) -> None:
    write_multi(slave_id, addr, [value & 0xFFFF])


# Canonical API with descriptive names and snake_case keyword arguments.
read_registers = read_regs
read_int16 = read_i16
read_uint32 = read_u32
read_float32 = read_f32


def write_register(
    slave_id: int,
    address: int,
    value: int,
    log_hex: bool = False,
    check_crc: bool = True,
) -> None:
    write_reg(slave_id, address, value, log_hex, check_crc)


def write_uint32(
    slave_id: int,
    address: int,
    value: int,
    log_hex: bool = False,
    check_crc: bool = True,
) -> None:
    write_u32(slave_id, address, value, log_hex, check_crc)


def write_two_uint32(
    slave_id: int,
    address: int,
    first_value: int,
    second_value: int,
    log_hex: bool = False,
    check_crc: bool = True,
) -> None:
    write_two_u32(
        slave_id, address, first_value, second_value, log_hex, check_crc
    )


def write_float32(
    slave_id: int,
    address: int,
    value: float,
    log_hex: bool = False,
) -> None:
    write_f32(slave_id, address, value, log_hex)


def connect(axis_count: int = 1) -> None:
    global ser, slaves
    manager.connect(axis_count)
    ser = manager.primary_serial
    slaves = manager.slaves


def disconnect() -> None:
    global ser
    try:
        manager.disconnect()
    finally:
        ser = None
