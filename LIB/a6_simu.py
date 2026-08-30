"""Software simulation and recording of the SimHub-driven A6 axes."""

import csv
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import RIG_CONFIG, SIMHUB_CONFIG
from .logging_config import get_logger
from .simrig_load_calibration import get_active_center_of_gravity

logger = get_logger("a6.simu")
SIMULATION_INTERVAL_S = 0.01
SIMHUB_DATA_DIR = Path(__file__).resolve().parent.parent / "SimHubData"
SIMHUB_DATA_MAX_AGE = timedelta(days=SIMHUB_CONFIG.data_retention_days)
HUB_AXIS_IDS = (4, 5, 6, 7)
GROUND_CONTACT_TOLERANCE_MM = 1e-6


def _solve_three_by_three(matrix, vector):
    """Solve a non-singular 3x3 system using Gaussian elimination."""
    rows = [list(matrix[index]) + [float(vector[index])] for index in range(3)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-12:
            raise ValueError("The configured SimRig geometry is degenerate")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for row in range(3):
            if row == column:
                continue
            factor = rows[row][column]
            rows[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(rows[row], rows[column], strict=False)
            ]
    return tuple(rows[index][3] for index in range(3))


@dataclass(frozen=True)
class SimRigState:
    ground_deviations_mm: tuple[float, float, float, float]
    ground_loads: tuple[float, float, float, float]
    lifted_axis: int | None


def _hub_points():
    front_width = RIG_CONFIG.distance_front_drives_left_to_right_mm
    rear_width = RIG_CONFIG.distance_rear_drives_left_to_right_mm
    wheelbase = RIG_CONFIG.distance_front_to_rear_drives_mm
    return (
        (wheelbase / 2.0, -front_width / 2.0),   # 4: FrontLeft
        (wheelbase / 2.0, front_width / 2.0),    # 5: FrontRight
        (-wheelbase / 2.0, rear_width / 2.0),    # 6: RearRight
        (-wheelbase / 2.0, -rear_width / 2.0),   # 7: RearLeft
    )


def _three_point_loads(points, lifted_index, center_of_gravity):
    supported = tuple(index for index in range(4) if index != lifted_index)
    matrix = (
        (1.0, 1.0, 1.0),
        tuple(points[index][0] for index in supported),
        tuple(points[index][1] for index in supported),
    )
    supported_loads = _solve_three_by_three(
        matrix, (1.0, center_of_gravity[0], center_of_gravity[1])
    )
    loads = [0.0] * 4
    for index, load in zip(supported, supported_loads, strict=False):
        loads[index] = load
    return tuple(loads)


def _four_point_loads(points, center_of_gravity):
    """Choose the minimum-norm solution for four coplanar supports.

    A perfectly rigid four-point support has one statically indeterminate
    degree of freedom. The minimum-norm solution is a deterministic symmetric
    reference that still fulfils force and moment equilibrium exactly.
    """
    equilibrium_rows = (
        (1.0, 1.0, 1.0, 1.0),
        tuple(point[0] for point in points),
        tuple(point[1] for point in points),
    )
    gram_matrix = tuple(
        tuple(
            sum(left * right for left, right in zip(left_row, right_row, strict=False))
            for right_row in equilibrium_rows
        )
        for left_row in equilibrium_rows
    )
    multipliers = _solve_three_by_three(
        gram_matrix, (1.0, center_of_gravity[0], center_of_gravity[1])
    )
    return tuple(
        sum(
            multipliers[row] * equilibrium_rows[row][column]
            for row in range(3)
        )
        for column in range(4)
    )


def calculate_simrig_state(hub_positions_mm):
    """Calculate ground deviations and normalized static support loads.

    The input order is FrontLeft, FrontRight, RearRight, RearLeft. The front
    feet define a fixed contact line. The mean rear actuator height fixes the
    remaining pitch degree of freedom. Positive rear values indicate a gap;
    negative values indicate that fixed contact at both front feet is not
    physically possible without another support lifting. Loads sum to one;
    a negative value means that the selected support set cannot statically
    carry the configured center of gravity without tensile ground force.
    """
    if len(hub_positions_mm) != 4:
        raise ValueError("Four hub positions are required for axes 4 through 7")

    points = _hub_points()
    wheelbase = RIG_CONFIG.distance_front_to_rear_drives_mm
    positions = tuple(float(position) for position in hub_positions_mm)
    # Plane z = offset + pitch*x + roll*y through both front feet and the
    # midpoint between the two rear actuator heights.
    reference_rows = (
        (1.0, *points[0]),
        (1.0, *points[1]),
        (1.0, -wheelbase / 2.0, 0.0),
    )
    reference_heights = (positions[0], positions[1], (positions[2] + positions[3]) / 2.0)
    plane = _solve_three_by_three(reference_rows, reference_heights)
    deviations = tuple(
        sum(coefficient * value for coefficient, value in zip(plane, (1.0, x, y), strict=False))
        - position
        for (x, y), position in zip(points, positions, strict=False)
    )
    # Avoid displaying floating-point noise for the constrained front feet.
    deviations = (0.0, 0.0, deviations[2], deviations[3])
    center_of_gravity = get_active_center_of_gravity()
    rear_gap = max(deviations[2:])
    if rear_gap > GROUND_CONTACT_TOLERANCE_MM:
        lifted_index = 2 if deviations[2] >= deviations[3] else 3
        loads = _three_point_loads(points, lifted_index, center_of_gravity)
        lifted_axis = HUB_AXIS_IDS[lifted_index]
    else:
        loads = _four_point_loads(points, center_of_gravity)
        lifted_axis = None
    return SimRigState(deviations, loads, lifted_axis)


def simrig_emu(hub_positions_mm):
    """Return signed ground deviations for axes 4..7 of a rigid SimRig."""
    return calculate_simrig_state(hub_positions_mm).ground_deviations_mm


@dataclass
class AxisCommand:
    target_position_mm: float
    accel_ms: int
    target_speed_mm_s: float
    decel_ms: int
    raw_target: int


@dataclass
class AxisState:
    command: AxisCommand
    calculated_actual_position_mm: float
    actual_velocity_mm_s: float = 0.0


class A6Simulator:
    """Thread-safe trapezoidal motion simulation for all configured axes."""

    def __init__(self, axis_count=None, interval_s=SIMULATION_INTERVAL_S):
        self.axis_count = axis_count or RIG_CONFIG.axis_count
        self.interval_s = float(interval_s)
        center_raw = (SIMHUB_CONFIG.position_min + SIMHUB_CONFIG.position_max) // 2
        initial = AxisCommand(0.0, 400, 0.0, 400, center_raw)
        self._states = [AxisState(initial, 0.0) for _ in range(self.axis_count)]
        self._read_actual_positions_mm = [0.0] * self.axis_count
        self._read_actual_position_timestamps = [None] * self.axis_count
        self._read_actual_position_monotonic = [None] * self.axis_count
        self._read_actual_positions_enabled = False
        self._pending = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._game_running = False
        self._ground_deviations_mm = (0.0,) * len(HUB_AXIS_IDS)
        self._ground_loads = (0.25,) * len(HUB_AXIS_IDS)
        self._lifted_axis = None
        self._csv_handle = None
        self._csv_writer = None

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._simulation_loop, name="A6Simulation", daemon=True
            )
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.interval_s * 4.0))
        with self._lock:
            self._thread = None
            self._stop_recording_locked()
            self._game_running = False

    def set_target(self, axis, target_position_mm, accel_ms,
                   target_speed_mm_s, decel_ms, *, raw_target=None):
        """Stage a command. It remains inactive until ``planner_trigger``."""
        if not 1 <= axis <= self.axis_count:
            raise ValueError(f"Invalid simulation axis: {axis}")
        with self._lock:
            if raw_target is None:
                raw_target = self._states[axis - 1].command.raw_target
            self._pending[axis] = AxisCommand(
                float(target_position_mm),
                max(1, int(accel_ms)),
                max(0.0, float(target_speed_mm_s)),
                max(1, int(decel_ms)),
                int(raw_target),
            )

    def planner_trigger(self, axis=0):
        """Activate staged commands, mirroring an A6 planner trigger."""
        with self._lock:
            if axis == 0:
                axes = tuple(self._pending)
            elif 1 <= axis <= self.axis_count:
                axes = (axis,)
            else:
                raise ValueError(f"Invalid simulation axis: {axis}")
            for axis_id in axes:
                command = self._pending.pop(axis_id, None)
                if command is not None:
                    self._states[axis_id - 1].command = command

    def set_game_running(self, running):
        """Handle SimHub's game state, including its rising edge."""
        running = bool(running)
        with self._lock:
            if running and not self._game_running:
                # Active commands are precisely those sent with a trigger;
                # deliberately ignore commands that are still pending.
                for state in self._states:
                    state.calculated_actual_position_mm = (
                        state.command.target_position_mm
                    )
                    state.actual_velocity_mm_s = 0.0
                self._start_recording_locked()
            elif not running and self._game_running:
                self._stop_recording_locked()
            self._game_running = running

    def set_read_actual_positions_enabled(self, enabled):
        with self._lock:
            self._read_actual_positions_enabled = bool(enabled)
            if not self._read_actual_positions_enabled:
                self._read_actual_positions_mm = [0.0] * self.axis_count
                self._read_actual_position_timestamps = [None] * self.axis_count
                self._read_actual_position_monotonic = [None] * self.axis_count

    def set_read_actual_position(self, axis, position_mm):
        if not 1 <= axis <= self.axis_count:
            raise ValueError(f"Invalid actual-position axis: {axis}")
        with self._lock:
            if self._read_actual_positions_enabled:
                self._read_actual_positions_mm[axis - 1] = float(position_mm)
                self._read_actual_position_timestamps[axis - 1] = datetime.now()
                self._read_actual_position_monotonic[axis - 1] = time.monotonic()

    def snapshot(self):
        with self._lock:
            return tuple(
                (state.command.target_position_mm,
                 state.calculated_actual_position_mm,
                 state.actual_velocity_mm_s)
                for state in self._states
            )

    def delete_expired_recordings(self):
        SIMHUB_DATA_DIR.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now().timestamp() - SIMHUB_DATA_MAX_AGE.total_seconds()
        for csv_file in SIMHUB_DATA_DIR.glob("*.csv"):
            try:
                if csv_file.stat().st_mtime < cutoff:
                    csv_file.unlink()
                    logger.info("Deleted expired recording %s", csv_file.name)
            except OSError as error:
                logger.warning("Could not process recording %s: %s", csv_file, error)

    def _simulation_loop(self):
        next_time = time.perf_counter()
        previous_time = next_time
        while not self._stop_event.is_set():
            now = time.perf_counter()
            dt = min(max(now - previous_time, 0.0), self.interval_s * 2.0)
            previous_time = now
            with self._lock:
                if self._game_running:
                    for state in self._states:
                        self._advance_axis(state, dt)
                    if self.axis_count >= HUB_AXIS_IDS[-1]:
                        rig_state = calculate_simrig_state(
                            tuple(
                                self._states[axis - 1].calculated_actual_position_mm
                                - RIG_CONFIG.axes[axis - 1].zero_offset_mm
                                for axis in HUB_AXIS_IDS
                            )
                        )
                        self._ground_deviations_mm = rig_state.ground_deviations_mm
                        self._ground_loads = rig_state.ground_loads
                        self._lifted_axis = rig_state.lifted_axis
                    self._record_snapshot_locked()
            next_time += self.interval_s
            remaining = next_time - time.perf_counter()
            if remaining > 0:
                self._stop_event.wait(remaining)
            else:
                next_time = time.perf_counter()

    @staticmethod
    def _advance_axis(state, dt):
        command = state.command
        position = state.calculated_actual_position_mm
        velocity = state.actual_velocity_mm_s
        distance = command.target_position_mm - position
        speed_limit = command.target_speed_mm_s
        if abs(distance) < 1e-9 or speed_limit <= 0.0:
            state.calculated_actual_position_mm = command.target_position_mm
            state.actual_velocity_mm_s = 0.0
            return

        direction = 1.0 if distance > 0.0 else -1.0
        accel = speed_limit / (command.accel_ms / 1000.0)
        decel = speed_limit / (command.decel_ms / 1000.0)
        moving_toward_target = velocity * direction > 0.0
        stopping_distance = velocity * velocity / (2.0 * decel) if moving_toward_target else 0.0
        if moving_toward_target and stopping_distance >= abs(distance):
            new_velocity = direction * max(0.0, abs(velocity) - decel * dt)
        else:
            desired_velocity = direction * speed_limit
            velocity_delta = accel * dt
            if velocity < desired_velocity:
                new_velocity = min(desired_velocity, velocity + velocity_delta)
            else:
                new_velocity = max(desired_velocity, velocity - velocity_delta)

        new_position = position + (velocity + new_velocity) * 0.5 * dt
        if (command.target_position_mm - new_position) * direction <= 0.0:
            new_position = command.target_position_mm
            new_velocity = 0.0
        state.calculated_actual_position_mm = new_position
        state.actual_velocity_mm_s = new_velocity

    def _start_recording_locked(self):
        if self._csv_handle is not None:
            return
        self.delete_expired_recordings()
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        csv_path = SIMHUB_DATA_DIR / f"{timestamp}.csv"
        suffix = 1
        while csv_path.exists():
            csv_path = SIMHUB_DATA_DIR / f"{timestamp}_{suffix:02d}.csv"
            suffix += 1
        self._csv_handle = csv_path.open(
            "x", encoding="utf-8-sig", newline="", buffering=1
        )
        self._csv_writer = csv.writer(
            self._csv_handle, delimiter=";", lineterminator="\n"
        )
        header = ["Timestamp"]
        # ValueN preserves the file contract used by the analysis dialog.
        header += [f"Value{axis}" for axis in range(1, self.axis_count + 1)]
        header += [f"TargetPosition{axis}" for axis in range(1, self.axis_count + 1)]
        header += [
            f"CalculatedActualPosition{axis}"
            for axis in range(1, self.axis_count + 1)
        ]
        header += [f"ActualPosition{axis}" for axis in range(1, self.axis_count + 1)]
        header += [
            f"ActualPositionTimestamp{axis}"
            for axis in range(1, self.axis_count + 1)
        ]
        header += [
            f"ActualPositionAgeMs{axis}"
            for axis in range(1, self.axis_count + 1)
        ]
        header += [f"ActualVelocity{axis}" for axis in range(1, self.axis_count + 1)]
        header += [f"GroundDeviation{axis}" for axis in HUB_AXIS_IDS]
        header += [f"GroundLoad{axis}" for axis in HUB_AXIS_IDS]
        self._csv_writer.writerow(header)
        logger.info("Recording simulated A6 data to %s", csv_path)

    def _stop_recording_locked(self):
        if self._csv_handle is None:
            return
        csv_path = Path(self._csv_handle.name)
        self._csv_handle.close()
        self._csv_handle = None
        self._csv_writer = None
        logger.info("Stopped SimHub recording %s", csv_path.name)

    def _record_snapshot_locked(self):
        if self._csv_writer is None:
            return
        recorded_at = datetime.now()
        recorded_monotonic = time.monotonic()
        timestamp = recorded_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        row = [timestamp]
        row += [state.command.raw_target for state in self._states]
        row += [
            f"{state.command.target_position_mm - RIG_CONFIG.axes[index].zero_offset_mm:.6f}"
            for index, state in enumerate(self._states)
        ]
        row += [
            f"{state.calculated_actual_position_mm - RIG_CONFIG.axes[index].zero_offset_mm:.6f}"
            for index, state in enumerate(self._states)
        ]
        row += [f"{position:.6f}" for position in self._read_actual_positions_mm]
        row += [
            read_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            if read_at is not None
            else ""
            for read_at in self._read_actual_position_timestamps
        ]
        row += [
            f"{max(0.0, (recorded_monotonic - read_at) * 1000.0):.3f}"
            if read_at is not None
            else ""
            for read_at in self._read_actual_position_monotonic
        ]
        row += [f"{state.actual_velocity_mm_s:.6f}" for state in self._states]
        row += [f"{deviation:.6f}" for deviation in self._ground_deviations_mm]
        row += [f"{load:.9f}" for load in self._ground_loads]
        self._csv_writer.writerow(row)

a6_simulator = A6Simulator()
