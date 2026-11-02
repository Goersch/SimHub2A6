# pyright: reportMissingImports=false
"""Generate wire and numerical oracles by executing the original Python code.

No serial ports, network sockets or live INI/CSV state are written. The Python
reference is loaded from the repository's Python source directory.
"""
from __future__ import annotations

import json
import logging
import random
import struct
import sys
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Python import Dialog as dialog  # noqa: E402
from Python import Leveling as leveling  # noqa: E402
from Python import ModBus as mb  # noqa: E402
from Python import SimHubCommands as commands  # noqa: E402
from Python.LIB import simrig_load_calibration as calibration  # noqa: E402
from Python.LIB.a6_driver import A6Driver  # noqa: E402
from Python.LIB.a6_motion import app_units_from_mm  # noqa: E402
from Python.LIB.a6_motion_controller import A6MotionController  # noqa: E402
from Python.LIB.a6_simu import (  # noqa: E402
    A6Simulator,
    AxisCommand,
    AxisState,
    calculate_simrig_state,
)
from Python.LIB.config import MODBUS_CONFIG, RIG_CONFIG  # noqa: E402

logging.disable(logging.CRITICAL)


def seal(data):
    return data + struct.pack("<H", mb.crc16_modbus(data))


class Wire:
    def __init__(self, high, save=False):
        self.registers = {
            a: {0x0A06: high, 0x0438: 9, 0x0439: 0, 0x0401: 0 if save else 1,
                0x410A: 1, 0x4005: 16, 0x4007: 100, 0x0312: 10}
            for a in range(1, 8)
        }
        self.frames = {c.port: [] for c in MODBUS_CONFIG.connections}

    def xfer(self, frame, **_kwargs):
        axis, fc, address, value = struct.unpack(">BBHH", frame[:6])
        for connection in MODBUS_CONFIG.connections:
            if axis == 0 or axis in connection.axes:
                self.frames[connection.port].append(frame.hex().upper())
        if fc == 3:
            words = [self.registers[axis].get(address + i, 0) for i in range(value)]
            return seal(bytes([axis, 3, len(words) * 2]) + struct.pack(f">{len(words)}H", *words))
        for a in range(1, 8) if axis == 0 else (axis,):
            regs = self.registers[a]
            if fc == 6:
                regs[address] = value
            elif fc == 16:
                for i, word in enumerate(struct.unpack(f">{value}H", frame[7:-2])):
                    regs[address + i] = word
            if fc == 6 and address == 0x0411:
                regs[0x410A] = 0 if value == 0 else 1
            if fc == 6 and address == 0x0419 and value == 1 and regs.get(0x0411) == 1:
                regs[0x4005] = 0
                regs[0x410A] = 1
                regs[0x4016], regs[0x4017] = regs.get(0x100B, 0), regs.get(0x100C, 0)
            if fc == 6 and address == 0x0415 and value == 1:
                regs[0x4016], regs[0x4017] = regs.get(0x1106, 0), regs.get(0x1107, 0)
        return frame if fc == 6 else seal(frame[:6])

    def queue(self, frame, response_validator=None, **kwargs):
        rx = self.xfer(frame, **kwargs)
        if response_validator is not None:
            response_validator(rx)


def scenario(name, axis, high):
    wire = Wire(high, name == "initialize-save")
    controller = A6MotionController(A6Driver(), sleep=lambda _: None)
    mb.manager._words_high_first = None
    with patch.object(mb, "xfer", wire.xfer), patch.object(mb, "queue_xfer", wire.queue):
        if name.startswith("initialize"):
            controller.initialize_parameters(axis)
        elif name == "start":
            controller.planner_start(axis, -30.1235)
        elif name == "parameters":
            for rpm, acc, dec in ((100, 300, 400), (100, 300, 400), (100, 500, 400),
                                   (100, 500, 600), (200, 700, 800), (-1, 1, 2)):
                controller.planner_set_parameters(axis, rpm, acc, dec, queued=True)
            controller.planner_trigger(0)
        elif name == "positions":
            for value in (-100, -30.1235, -0.0005, 0, 0.0005, 0.0015, 12.5, 12.5, 100):
                controller.planner_set_position_mm(axis, value, trigger=False, queued=True)
            controller.planner_trigger(0)
            controller.planner_set_position_mm(axis, 100)
        elif name == "home":
            controller.start_homing(axis, ignore_status=True, trigger=False)
            controller.trigger_homing(0)
            assert controller.homing_complete(axis)
        elif name == "stop":
            controller.planner_stop(axis)
        elif name == "move":
            assert controller.move_to_mm(axis, -12.5)
        else:
            raise AssertionError(name)
    return {"Name": name, "Axis": axis, "High": high == 1, "Frames": wire.frames}


def engine_scenario(high):
    wire = Wire(high)
    controller = A6MotionController(A6Driver(), sleep=lambda _: None)
    mb.manager._words_high_first = None
    with patch.object(mb, "xfer", wire.xfer), patch.object(mb, "queue_xfer", wire.queue), \
         patch.object(mb, "connect", lambda _: setattr(mb, "slaves", 7)), \
         patch.object(mb, "disconnect", lambda: None), \
         patch.object(commands, "motion_controller", controller), \
         patch.object(commands, "runtime_state", commands.SimHubRuntimeState()), \
         patch.object(commands, "levelingOffset", [0.0] * 7), \
         patch.object(leveling, "load_leveling_offsets", return_value=False), \
         patch("time.sleep", lambda _: None):
        commands.handle_init()
        commands.handle_end()
    return {"Name": "engine-lifecycle", "Axis": 0, "High": bool(high), "Frames": wire.frames}


def leveling_scenario(high):
    class LoadWire(Wire):
        def __init__(self):
            super().__init__(high)
            self.offsets = [0.0] * 7

        def xfer(self, frame, **kwargs):
            axis, fc, address, _ = struct.unpack(">BBHH", frame[:6])
            if fc == 16 and address == 0x1106:
                words = struct.unpack(">HH", frame[7:11])
                first, second = words if high else words[::-1]
                value = (first << 16) | second
                if value & 0x80000000:
                    value -= 0x100000000
                self.offsets[axis - 1] = value / (1000 * 10 / RIG_CONFIG.axis(axis).spindle_pitch_mm) - RIG_CONFIG.axis(axis).zero_offset_mm
            if fc == 3 and address == 0x4007 and axis >= 4:
                self.registers[axis][address] = round([100, 70, 80, 90][axis - 4] + self.offsets[axis - 1] * 100)
            return super().xfer(frame, **kwargs)

    wire = LoadWire()
    controller = A6MotionController(A6Driver(), sleep=lambda _: None)
    mb.manager._words_high_first = None
    offsets = [0.0] * 7
    with patch.object(mb, "xfer", wire.xfer), patch.object(mb, "queue_xfer", wire.queue), \
         patch.object(commands, "runtime_state", commands.SimHubRuntimeState()), \
         patch.object(commands, "levelingOffset", offsets), \
         patch.object(leveling, "levelingOffset", offsets), \
         patch.object(leveling, "motion_controller", controller), \
         patch.object(leveling, "load_leveling_offsets", return_value=False), \
         patch.object(leveling, "save_leveling_offsets", return_value=True), \
         patch.object(leveling, "_log", lambda _: None), \
         patch.object(leveling, "_wait_for_leveling_delay", lambda _: None):
        leveling.leveling()
    return {"Name": "leveling", "Axis": 0, "High": bool(high), "Frames": wire.frames}


def generate():
    randomizer = random.Random(70419)
    frames = []
    for _ in range(12000):
        axis, address = randomizer.randrange(8), randomizer.randrange(65536)
        operation = randomizer.choice(("read", "write", "multi"))
        if operation == "read":
            values = [randomizer.randrange(1, 126)]
            frame = mb.build_read(axis, address, values[0])
        elif operation == "write":
            values = [randomizer.randrange(-0x80000000, 0x80000000)]
            frame = mb.build_write(axis, address, values[0])
        else:
            values = [randomizer.randrange(-65536, 65536) for _ in range(randomizer.randrange(1, 20))]
            frame = mb.build_write_multi(axis, address, values)
        frames.append({"Operation": operation, "Axis": axis, "Address": address, "Values": values, "Hex": frame.hex().upper()})
    conversions = []
    for a in range(1, 8):
        for mm in [-100, -0.0015, -0.0005, 0, 0.0005, 0.0015, 100] + [randomizer.uniform(-200, 200) for _ in range(1000)]:
            conversions.append({"Axis": a, "Mm": mm, "Units": app_units_from_mm(a, mm)})
    # A saved local calibration must not make the oracle machine-dependent.
    geometry = []
    with patch.object(calibration, "_active_calibration", None):
        for _ in range(200):
            pos = [randomizer.uniform(-100, 100) for _ in range(4)]
            state = calculate_simrig_state(pos)
            geometry.append({"Positions": pos, "Ground": state.ground_deviations_mm, "Loads": state.ground_loads})
    dynamics = []
    for _ in range(400):
        target, position, velocity = [randomizer.uniform(-100, 100) for _ in range(3)]
        speed = randomizer.uniform(0, 300)
        acc, dec = randomizer.randrange(100, 1000), randomizer.randrange(100, 1000)
        dt = randomizer.uniform(0, 0.05)
        state = AxisState(AxisCommand(target, acc, speed, dec, 65535), position, velocity)
        A6Simulator._advance_axis(state, dt)
        dynamics.append(dict(Target=target, Position=position, Velocity=velocity, Speed=speed,
                             Accel=acc, Decel=dec, Dt=dt, ExpectedPosition=state.calculated_actual_position_mm,
                             ExpectedVelocity=state.actual_velocity_mm_s))
    targets = []
    for a in range(1, 8):
        for raw in (0, 1, 65535, 65536, 131070, 131071):
            for offset in (0.0, -1.2345, 6.0):
                with patch.object(commands, "levelingOffset", [offset] * 7):
                    targets.append(dict(Axis=a, Raw=raw, Offset=offset, Mm=commands.target_position_mm(a, raw)))
    result = {"Frames": frames, "Conversions": conversions, "Geometry": geometry, "Dynamics": dynamics,
              "Targets": targets, "Scenarios": [scenario(name, a, high) for high in (0, 1)
                  for a in range(1, 8) for name in ("initialize", "initialize-save", "start", "parameters", "positions", "home", "stop", "move")]}
    result["Lifecycle"] = [engine_scenario(high) for high in (0, 1)]
    result["Leveling"] = [leveling_scenario(high) for high in (0, 1)]
    view = cast(dialog.Dialog, MagicMock(spec=dialog.Dialog))
    result["UiStates"] = [
        {"Reason": reason, "Text": dialog.Dialog._format_error_state(view, reason, axis=1)}
        for reason in (-1, 0, 1, 2, 3, 4, 5, 6, 65535)
    ]
    destination = ROOT / "CSharp/Tests/reference.json"
    destination.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    print(f"Python oracle: {len(frames)} RTU frames, {len(conversions)} conversions, {len(result['Scenarios'])} motion traces")


if __name__ == "__main__":
    generate()
