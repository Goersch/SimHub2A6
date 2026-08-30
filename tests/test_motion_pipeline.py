import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from SimHub2A6 import ModBus as modbus
from SimHub2A6 import SimHub2SimRig, SimHubCommands
from SimHub2A6.LIB import a6_registers as reg
from SimHub2A6.LIB.a6_driver import A6Driver
from SimHub2A6.LIB.a6_motion import pitch_rpm_from_rpm, rpm_from_mm_per_second
from SimHub2A6.LIB.a6_motion_controller import A6MotionController


class OneCycleStopEvent:
    def __init__(self):
        self._checks = 0

    def is_set(self):
        self._checks += 1
        return self._checks > 1


class CoordinatedSerial:
    instances = {}
    started = {}
    completed = {}
    release = {}
    failing_axes = set()

    @classmethod
    def configure(cls, ports, failing_axes=()):
        cls.instances = {}
        cls.started = {port: threading.Event() for port in ports}
        cls.completed = {port: threading.Event() for port in ports}
        cls.release = {port: threading.Event() for port in ports}
        cls.failing_axes = set(failing_axes)

    def __init__(self, port, *_args, **_kwargs):
        self.port = port
        self.attempts = []
        self.writes = []
        self._response = b""
        self.closed = False
        type(self).instances[port] = self

    def reset_input_buffer(self):
        pass

    def write(self, data):
        frame = bytes(data)
        axis = frame[0]
        self.attempts.append(frame)
        if axis != 0:
            type(self).started[self.port].set()
            if not type(self).release[self.port].wait(2.0):
                raise TimeoutError(f"Test did not release {self.port}")
            if axis in type(self).failing_axes:
                raise OSError(f"Simulated write failure for axis {axis}")
        self.writes.append(frame)
        response_pdu = frame[:6]
        self._response = response_pdu + modbus.crc16_modbus(response_pdu).to_bytes(
            2, "little"
        )
        type(self).completed[self.port].set()

    def flush(self):
        pass

    @property
    def in_waiting(self):
        return len(self._response)

    def read(self, size):
        response = self._response[:size]
        self._response = self._response[size:]
        return response

    def close(self):
        self.closed = True


class MotionPipelineTests(unittest.TestCase):
    PORTS = ("COM-A", "COM-B")

    def setUp(self):
        CoordinatedSerial.configure(self.PORTS)
        self.manager = modbus.ModbusManager(
            connection_configs=(
                (self.PORTS[0], frozenset({1})),
                (self.PORTS[1], frozenset({2})),
            ),
            serial_factory=CoordinatedSerial,
            inter_frame_delay=0,
            queue_drain_timeout=2.0,
            task_wait_timeout=0.5,
            worker_join_timeout=0.5,
        )
        self.manager.connect(2)
        self.manager._words_high_first = True
        self.controller = A6MotionController(A6Driver(modbus), sleep=lambda _delay: None)

    def tearDown(self):
        for release in CoordinatedSerial.release.values():
            release.set()
        self.manager.disconnect()

    def _pipeline_context(self):
        state = SimHubCommands.SimHubRuntimeState(initialized=True)
        simulator = MagicMock()
        latest_values = [SimHubCommands.maxPos] * 2 + [int(SimHubCommands.maxPos / 2)] * 5
        stack = ExitStack()
        stack.enter_context(patch.object(modbus, "manager", self.manager))
        stack.enter_context(
            patch.multiple(
                SimHubCommands,
                motion_controller=self.controller,
                runtime_state=state,
                axisCount=2,
                enableAxis=[1, 1, 0, 0, 0, 0, 0],
                levelingOffset=[0.0] * 7,
                a6_simulator=simulator,
            )
        )
        stack.enter_context(patch.object(SimHubCommands.Grease, "greaseActive", False))
        stack.enter_context(patch.object(SimHubCommands.Leveling, "levelingActive", False))
        stack.enter_context(
            patch.multiple(
                SimHub2SimRig,
                motion_controller=self.controller,
                latestValues=latest_values,
                latestLock=threading.Lock(),
                stopEvent=OneCycleStopEvent(),
                a6_simulator=simulator,
            )
        )
        stack.enter_context(patch.object(SimHub2SimRig.Analyse, "analyseActive", False))
        fake_time = SimpleNamespace(
            perf_counter=lambda: 0.0,
            sleep=MagicMock(),
            time=lambda: 1000.0,
        )
        stack.enter_context(patch.object(SimHub2SimRig, "time", fake_time))
        return stack, simulator

    @staticmethod
    def _frames(port, axis):
        return [
            frame
            for frame in CoordinatedSerial.instances[port].writes
            if frame[0] == axis
        ]

    def test_parallel_axes_with_cached_parameters_wait_then_trigger_once(self):
        axis_1_rpm = rpm_from_mm_per_second(1, SimHubCommands.SPEED_MM_S[0])
        self.controller.state.parameter_cache[1] = {
            "rpm": pitch_rpm_from_rpm(1, axis_1_rpm),
            "accel_ms": SimHubCommands.ACC_TIME_MS[0],
            "decel_ms": SimHubCommands.DEC_TIME_MS[0],
        }

        stack, simulator = self._pipeline_context()
        with stack, patch.object(
            self.controller,
            "planner_trigger",
            wraps=self.controller.planner_trigger,
        ) as trigger:
            sender = threading.Thread(target=SimHub2SimRig.sender_thread)
            sender.start()

            self.assertTrue(CoordinatedSerial.started[self.PORTS[0]].wait(1.0))
            self.assertTrue(CoordinatedSerial.started[self.PORTS[1]].wait(1.0))
            self.assertEqual(self._frames(self.PORTS[0], 0), [])
            self.assertEqual(self._frames(self.PORTS[1], 0), [])

            CoordinatedSerial.release[self.PORTS[0]].set()
            self.assertTrue(CoordinatedSerial.completed[self.PORTS[0]].wait(1.0))
            self.assertEqual(self._frames(self.PORTS[0], 0), [])
            self.assertEqual(self._frames(self.PORTS[1], 0), [])

            CoordinatedSerial.release[self.PORTS[1]].set()
            sender.join(2.0)

            self.assertFalse(sender.is_alive())
            trigger.assert_called_once_with(0)
            simulator.planner_trigger.assert_called_once_with(0)

        axis_1_frames = self._frames(self.PORTS[0], 1)
        axis_2_frames = self._frames(self.PORTS[1], 2)
        self.assertEqual([int.from_bytes(frame[2:4], "big") for frame in axis_1_frames], [reg.C11_06])
        self.assertEqual(
            [int.from_bytes(frame[2:4], "big") for frame in axis_2_frames],
            [reg.C11_08, reg.C11_0A, reg.C11_06],
        )
        for port in self.PORTS:
            broadcast_frames = self._frames(port, 0)
            self.assertEqual(len(broadcast_frames), 2)
            self.assertEqual(
                [int.from_bytes(frame[4:6], "big") for frame in broadcast_frames],
                [1, 0],
            )

    def test_failed_axis_queue_prevents_broadcast_and_simulator_trigger(self):
        CoordinatedSerial.failing_axes = {2}
        for release in CoordinatedSerial.release.values():
            release.set()

        stack, simulator = self._pipeline_context()
        with stack:
            SimHub2SimRig.sender_thread()

        self.assertEqual(self._frames(self.PORTS[0], 0), [])
        self.assertEqual(self._frames(self.PORTS[1], 0), [])
        simulator.planner_trigger.assert_not_called()


if __name__ == "__main__":
    unittest.main()
