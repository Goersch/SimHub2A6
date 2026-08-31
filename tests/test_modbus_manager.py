import threading
import time
import unittest
from unittest.mock import patch

from SimHub2A6 import ModBus as modbus


class FakeSerial:
    instances = {}

    def __init__(self, port, *_args, **_kwargs):
        self.port = port
        self.writes = []
        self.closed = False
        self.instances[port] = self

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(bytes(data))

    def flush(self):
        pass

    @property
    def in_waiting(self):
        return 0

    def read(self, _size):
        return b""

    def close(self):
        self.closed = True


class ModbusManagerTests(unittest.TestCase):
    def test_register_write_reports_standard_modbus_exception(self):
        response = bytes([1, modbus.FC_WRITE | 0x80, 0x04])
        response += modbus.crc16_modbus(response).to_bytes(2, "little")

        with patch.object(modbus, "xfer", return_value=response):
            with self.assertRaisesRegex(
                RuntimeError,
                r"Modbus exception 0x04 \(function 0x06\)",
            ):
                modbus.write_reg(1, 0x1100, 3)

    def test_register_write_retries_one_crc_error(self):
        valid_response = modbus.build_write(4, 0x1100, 3)
        invalid_response = valid_response[:-1] + bytes([valid_response[-1] ^ 0xFF])

        with patch.object(
            modbus,
            "xfer",
            side_effect=[invalid_response, valid_response],
        ) as transfer:
            modbus.write_reg(4, 0x1100, 3)

        self.assertEqual(transfer.call_count, 2)

    def test_register_write_reports_a_repeated_crc_error(self):
        valid_response = modbus.build_write(4, 0x1100, 3)
        invalid_response = valid_response[:-1] + bytes([valid_response[-1] ^ 0xFF])

        with patch.object(modbus, "xfer", return_value=invalid_response) as transfer:
            with self.assertRaises(modbus.ModbusCrcError):
                modbus.write_reg(4, 0x1100, 3)

        self.assertEqual(transfer.call_count, modbus.WRITE_CRC_RETRIES + 1)

    def test_register_write_recovers_after_multiple_crc_errors(self):
        valid_response = modbus.build_write(2, 0x1100, 3)
        invalid_response = valid_response[:-1] + bytes([valid_response[-1] ^ 0xFF])

        with patch.object(
            modbus,
            "xfer",
            side_effect=[invalid_response, invalid_response, valid_response],
        ) as transfer, patch.object(modbus.time, "sleep"):
            modbus.write_reg(2, 0x1100, 3)

        self.assertEqual(transfer.call_count, 3)

    def test_disconnect_closes_every_port_when_one_close_fails(self):
        class CloseFailingSerial(FakeSerial):
            def close(self):
                super().close()
                if self.port == "COM1":
                    raise OSError("close failed")

        CloseFailingSerial.instances = {}
        FakeSerial.instances = CloseFailingSerial.instances
        manager = modbus.ModbusManager(
            connection_configs=(
                ("COM1", frozenset({1})),
                ("COM2", frozenset({2})),
            ),
            serial_factory=CloseFailingSerial,
            inter_frame_delay=0,
            task_wait_timeout=0.2,
        )
        manager.connect(2)

        with self.assertRaisesRegex(OSError, "close failed"):
            manager.disconnect()

        self.assertTrue(CloseFailingSerial.instances["COM1"].closed)
        self.assertTrue(CloseFailingSerial.instances["COM2"].closed)
        self.assertFalse(manager.connected)

    def test_routes_axes_and_broadcasts_to_all_connections(self):
        FakeSerial.instances = {}
        manager = modbus.ModbusManager(
            connection_configs=(("COM1", frozenset({1, 2, 3})), ("COM2", frozenset({4}))),
            serial_factory=FakeSerial,
            inter_frame_delay=0,
            task_wait_timeout=0.2,
        )
        manager.connect(7)
        try:
            manager.xfer(
                modbus.build_write(4, 1, 2), log_hex=False, wait_for_response=False
            )
            self.assertEqual(FakeSerial.instances["COM2"].writes[-1][0], 4)
            manager.xfer(
                modbus.build_write(0, 1, 2), log_hex=False, wait_for_response=False
            )
            self.assertTrue(
                all(serial.writes[-1][0] == 0 for serial in FakeSerial.instances.values())
            )
        finally:
            manager.disconnect()

    def test_queued_transfers_run_in_parallel_and_broadcast_holds_submission_gate(self):
        release_unicast = threading.Event()
        started_ports = set()
        started_lock = threading.Lock()

        class BlockingSerial(FakeSerial):
            def write(self, data):
                frame = bytes(data)
                self.writes.append(frame)
                if frame[0] != 0 and frame[3] == 1:
                    with started_lock:
                        started_ports.add(self.port)
                    release_unicast.wait(1.0)

        BlockingSerial.instances = {}
        FakeSerial.instances = BlockingSerial.instances
        manager = modbus.ModbusManager(
            connection_configs=(("COM1", frozenset({1})), ("COM2", frozenset({2}))),
            serial_factory=BlockingSerial,
            inter_frame_delay=0,
            task_wait_timeout=0.5,
        )
        manager.connect(2)
        broadcast_done = threading.Event()
        late_submit_done = threading.Event()
        try:
            manager.queue_xfer(
                modbus.build_write(1, 1, 10),
                log_hex=False,
                wait_for_response=False,
            )
            manager.queue_xfer(
                modbus.build_write(2, 1, 20),
                log_hex=False,
                wait_for_response=False,
            )

            deadline = time.monotonic() + 0.5
            while len(started_ports) < 2 and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(started_ports, {"COM1", "COM2"})

            def broadcast():
                manager.xfer(
                    modbus.build_write(0, 2, 1),
                    log_hex=False,
                    wait_for_response=False,
                )
                broadcast_done.set()

            broadcast_thread = threading.Thread(target=broadcast)
            broadcast_thread.start()
            time.sleep(0.02)

            def late_submit():
                manager.queue_xfer(
                    modbus.build_write(1, 3, 30),
                    log_hex=False,
                    wait_for_response=False,
                )
                late_submit_done.set()

            late_thread = threading.Thread(target=late_submit)
            late_thread.start()
            time.sleep(0.02)
            self.assertFalse(broadcast_done.is_set())
            self.assertFalse(late_submit_done.is_set())

            release_unicast.set()
            broadcast_thread.join(0.5)
            late_thread.join(0.5)
            self.assertTrue(broadcast_done.is_set())
            self.assertTrue(late_submit_done.is_set())
            self.assertEqual(
                [frame[0] for frame in BlockingSerial.instances["COM1"].writes[:2]],
                [1, 0],
            )
            self.assertEqual(
                [frame[0] for frame in BlockingSerial.instances["COM2"].writes],
                [2, 0],
            )
        finally:
            release_unicast.set()
            manager.disconnect()
