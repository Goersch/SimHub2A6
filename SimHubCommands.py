import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):
    package_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(package_dir.parent))
    __package__ = package_dir.name

from . import Grease, Leveling
from .LIB.a6_motion import rpm_from_mm_per_second
from .LIB.a6_motion_controller import motion_controller
from .LIB.a6_simu import a6_simulator
from .LIB.config import CONTROL_CONFIG, RIG_CONFIG, SIMHUB_CONFIG
from .LIB.logging_config import get_logger

logger = get_logger("simhub.commands")

AXIS_COUNT = RIG_CONFIG.axis_count
MAX_AXIS = len(RIG_CONFIG.axes)
ENABLED_AXES = [int(axis.enabled) for axis in RIG_CONFIG.axes]
MAX_STROKE_MM = [axis.stroke_mm for axis in RIG_CONFIG.axes]
ZERO_OFFSET_MM = [axis.zero_offset_mm for axis in RIG_CONFIG.axes]
levelingOffset = Leveling.levelingOffset
SPEED_MM_S = [axis.speed_mm_s for axis in RIG_CONFIG.axes]
ACC_TIME_MS = [axis.acc_time_ms for axis in RIG_CONFIG.axes]
DEC_TIME_MS = [axis.dec_time_ms for axis in RIG_CONFIG.axes]
MAX_RAW_POSITION = RIG_CONFIG.limits.raw_position_max

# Compatibility aliases for existing UI/service modules.
axisCount = AXIS_COUNT
maxAxis = MAX_AXIS
enableAxis = ENABLED_AXES
maxStroke = MAX_STROKE_MM
zeroOffset = ZERO_OFFSET_MM
maxSpeed = SPEED_MM_S
accTime = ACC_TIME_MS
decTime = DEC_TIME_MS
maxPos = MAX_RAW_POSITION
HUB_AXIS_FROM = CONTROL_CONFIG.hub_axis_from
HUB_AXIS_TO = CONTROL_CONFIG.hub_axis_to
HUB_STATUS_POLL_INTERVAL = CONTROL_CONFIG.hub_status_poll_interval_s
DYNAMIC_ACCEL_STEP_MS = CONTROL_CONFIG.dynamic_accel_step_ms
DYNAMIC_PARAMETER_UPDATE_INTERVAL_S = (
    CONTROL_CONFIG.dynamic_parameter_update_interval_s
)
CENTER_RPM = CONTROL_CONFIG.center_rpm
CENTER_ACCEL_MS = CONTROL_CONFIG.center_accel_ms
CENTER_DECEL_MS = CONTROL_CONFIG.center_decel_ms
MAINTENANCE_MIDDLE_RPM = CONTROL_CONFIG.maintenance_middle_rpm
MAINTENANCE_HUB_RPM = CONTROL_CONFIG.maintenance_hub_rpm
AXIS_WAIT_TIMEOUT_S = CONTROL_CONFIG.axis_wait_timeout_s
HOMING_WAIT_TIMEOUT_S = CONTROL_CONFIG.homing_wait_timeout_s
WAIT_POLL_INTERVAL_S = CONTROL_CONFIG.wait_poll_interval_s
SHUTDOWN_CENTER_SETTLE_S = 2.0


@dataclass
class SimHubRuntimeState:
    initialized: bool = False
    initializing: bool = False
    hub_fault_active: bool = False
    hub_fault_last_check: float = 0.0
    hub_fault_details: dict = field(default_factory=dict)
    hub_status_index: int = 0
    dynamic_parameter_next_update: list = field(
        default_factory=lambda: [0.0] * maxAxis
    )
    positions_enabled: bool = True
    maintenance_active: bool = False
    shutdown_active: bool = False
    simhub_positions_armed: bool = True
    position_lock: threading.RLock = field(default_factory=threading.RLock)
    previous_raw_positions: list = field(
        default_factory=lambda: [int(maxPos / 2)] * maxAxis
    )
    previous_positions: list = field(default_factory=lambda: [0.0] * maxAxis)
    maintenance_planner_axes: set[int] = field(default_factory=set)


runtime_state = SimHubRuntimeState()

_LEGACY_STATE_NAMES = {
    "init": "initialized",
    "initActive": "initializing",
    "hubAxesFaultActive": "hub_fault_active",
    "hubAxesFaultLastCheck": "hub_fault_last_check",
    "hubAxesFaultDetails": "hub_fault_details",
    "hubAxesStatusIndex": "hub_status_index",
    "dynamicParameterNextUpdate": "dynamic_parameter_next_update",
    "simHubPositionsEnabled": "positions_enabled",
    "simHubPositionLock": "position_lock",
    "prevPos2": "previous_raw_positions",
    "prevPos": "previous_positions",
}


def __getattr__(name):
    state_name = _LEGACY_STATE_NAMES.get(name)
    if state_name is not None:
        return getattr(runtime_state, state_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def _axis_is_hub_axis(axis:int):
    return HUB_AXIS_FROM <= axis <= HUB_AXIS_TO


def set_simhub_positions_enabled(enabled:bool):
    with runtime_state.position_lock:
        runtime_state.positions_enabled = bool(enabled)
    mode = "SimHub" if runtime_state.positions_enabled else "center position"
    logger.info("Operating mode: %s", mode)


def set_maintenance_active(active: bool) -> None:
    """Block SimHub motion while the maintenance dialog owns the axes."""
    with runtime_state.position_lock:
        runtime_state.maintenance_active = bool(active)
        if active:
            # Require a fresh POSITIONS packet after maintenance. Otherwise the
            # sender could apply a stale target as soon as the dialog closes.
            runtime_state.simhub_positions_armed = False
    logger.info("Maintenance mode %s", "active" if active else "inactive")


def simhub_telegrams_blocked() -> bool:
    # This check runs in the high-frequency sender loop and must not wait for a
    # maintenance operation that currently owns position_lock.
    return runtime_state.maintenance_active or runtime_state.shutdown_active


def shutdown_in_progress() -> bool:
    return runtime_state.shutdown_active


def arm_simhub_positions() -> bool:
    """Allow motion only for a packet received outside maintenance mode."""
    with runtime_state.position_lock:
        if runtime_state.maintenance_active or runtime_state.shutdown_active:
            return False
        runtime_state.simhub_positions_armed = True
        return True


def center_all_axes():
    with runtime_state.position_lock:
        for axis in enabled_axes():
            # A6_planner_set_parameters converts the requested motor speed
            # internally with pitch_rpm_from_rpm(axis, CENTER_RPM).
            motion_controller.planner_set_parameters(
                axis,
                CENTER_RPM,
                CENTER_ACCEL_MS,
                CENTER_DECEL_MS,
            )
        all_axis_to_position(int(maxPos / 2))


def move_axes_for_maintenance(first_axis:int, last_axis:int, position:int):
    """Move a maintenance axis group after safely entering planner mode."""
    requested_axes = list(range(first_axis, last_axis + 1))
    inactive_axes = [axis for axis in requested_axes if not axis_enabled(axis)]
    if inactive_axes:
        raise RuntimeError(f"Axes not enabled: {inactive_axes}")

    with runtime_state.position_lock:
        unhomed_axes = [
            axis
            for axis in requested_axes
            if not motion_controller.homing_completed(axis)
        ]
        if unhomed_axes:
            raise RuntimeError(f"Axes not homed: {unhomed_axes}")

        for axis in requested_axes:
            current_position_mm = motion_controller.read_position_mm(axis)
            if axis not in runtime_state.maintenance_planner_axes:
                motion_controller.planner_start(axis, current_position_mm)
                runtime_state.maintenance_planner_axes.add(axis)
            maintenance_rpm = (
                MAINTENANCE_MIDDLE_RPM
                if axis in (1, 2, 3)
                else MAINTENANCE_HUB_RPM
            )
            motion_controller.planner_set_parameters(
                axis,
                maintenance_rpm,
                ACC_TIME_MS[axis - 1],
                DEC_TIME_MS[axis - 1],
            )
            runtime_state.previous_positions[axis - 1] = current_position_mm
        is_hub_move = first_axis == HUB_AXIS_FROM and last_axis == HUB_AXIS_TO
        if not is_hub_move:
            axis_to_position(first_axis, last_axis, position)
            return

        # S-ON releases the motor brakes.  Once all four hub axes have reached
        # the requested maintenance position, S-ON is removed so the brakes
        # carry the rig until the next command.
        try:
            _set_hub_servos_enabled(requested_axes, True)
            axis_to_position(first_axis, last_axis, position)
        except BaseException:
            try:
                _set_hub_servos_enabled(requested_axes, False)
            except Exception:
                logger.exception("Failed to apply all hub brakes after move error")
            raise
        else:
            _set_hub_servos_enabled(requested_axes, False)


def ensure_maintenance_planners(axes) -> None:
    """Start missing planners at their current positions for maintenance tools."""
    requested_axes = list(axes)
    with runtime_state.position_lock:
        unhomed_axes = [
            axis
            for axis in requested_axes
            if not axis_enabled(axis) or not motion_controller.homing_completed(axis)
        ]
        if unhomed_axes:
            raise RuntimeError(f"Axes not homed: {unhomed_axes}")
        for axis in requested_axes:
            if axis in runtime_state.maintenance_planner_axes:
                continue
            current_position_mm = motion_controller.read_position_mm(axis)
            motion_controller.planner_start(axis, current_position_mm)
            runtime_state.maintenance_planner_axes.add(axis)
            runtime_state.previous_positions[axis - 1] = current_position_mm


def restore_planner_mode_after_maintenance() -> None:
    """Resume existing planners and restart only planners stopped by homing."""
    with runtime_state.position_lock:
        axes = enabled_axes()
        current_positions = {
            axis: motion_controller.read_position_mm(axis) for axis in axes
        }
        for axis in axes:
            current_position_mm = current_positions[axis]
            if axis in runtime_state.maintenance_planner_axes:
                # The planner is already configured. Rewriting C11.00 while it
                # is active is rejected by the drive with Modbus exception 04.
                # Hub maintenance may have disabled S-ON to apply the brakes,
                # so enabling the servo is the only restoration needed here.
                motion_controller.set_servo_enabled(axis, True)
            else:
                # Homing stops the planner and removes the axis from the set.
                motion_controller.planner_start(axis, current_position_mm)
            runtime_state.previous_positions[axis - 1] = current_position_mm
        runtime_state.maintenance_planner_axes.clear()


def _set_hub_servos_enabled(hub_axes, enabled: bool) -> None:
    """Set every hub servo and still attempt the remaining axes after an error."""
    first_error = None
    for axis in hub_axes:
        try:
            motion_controller.set_servo_enabled(axis, enabled)
        except Exception as exc:
            if first_error is None:
                first_error = exc
            logger.exception(
                "Failed to %s hub brake on axis %s",
                "release" if enabled else "apply",
                axis,
            )
    if first_error is not None:
        raise first_error


def maintenance_homing_status():
    """Return homing status for front, middle, rear and all hub axes."""
    with runtime_state.position_lock:
        hub_axes = range(HUB_AXIS_FROM, HUB_AXIS_TO + 1)
        hub_homed = all(
            axis_enabled(axis) and motion_controller.homing_completed(axis)
            for axis in hub_axes
        )
        front_homed = axis_enabled(1) and motion_controller.homing_completed(1)
        middle_homed = axis_enabled(2) and motion_controller.homing_completed(2)
        rear_homed = axis_enabled(3) and motion_controller.homing_completed(3)
    return front_homed, middle_homed, rear_homed, hub_homed


def home_axes_for_maintenance(first_axis:int, last_axis:int):
    """Home every requested maintenance axis and update its cached position."""
    requested_axes = list(range(first_axis, last_axis + 1))
    shared_hub_trigger = (
        first_axis == HUB_AXIS_FROM and last_axis == HUB_AXIS_TO
    )
    inactive_axes = [axis for axis in requested_axes if not axis_enabled(axis)]
    if inactive_axes:
        raise RuntimeError(f"Axes not enabled: {inactive_axes}")

    with runtime_state.position_lock:
        for axis in requested_axes:
            runtime_state.maintenance_planner_axes.discard(axis)
            motion_controller.planner_stop(axis)
        for axis in requested_axes:
            motion_controller.start_homing(
                axis,
                ignore_status=True,
                trigger=not shared_hub_trigger,
            )
        if shared_hub_trigger:
            # All hub axes are prepared first; the broadcast creates one
            # shared trigger edge on every configured Modbus connection.
            time.sleep(1)
            motion_controller.trigger_homing(0)
        wait_for_homing(first_axis, last_axis)
        for axis in requested_axes:
            runtime_state.previous_positions[axis - 1] = RIG_CONFIG.limits.homing_mm
            runtime_state.previous_raw_positions[axis - 1] = SIMHUB_CONFIG.position_min


def home_hub_axes_for_maintenance():
    """Home all four hub axes (4-7)."""
    home_axes_for_maintenance(HUB_AXIS_FROM, HUB_AXIS_TO)


def home_front_axis_for_maintenance():
    """Home the front axis (axis 1)."""
    home_axes_for_maintenance(1, 1)


def home_middle_axis_for_maintenance():
    """Home the middle axis (axis 2)."""
    home_axes_for_maintenance(2, 2)


def home_rear_axis_for_maintenance():
    """Home the rear axis (axis 3)."""
    home_axes_for_maintenance(3, 3)


def _center_hub_axes_after_fault(hub_axes):
    center_position = int(maxPos / 2)
    position_updated = False

    for axis in hub_axes:
        try:
            motion_controller.planner_set_parameters(
                axis,
                CENTER_RPM,
                CENTER_ACCEL_MS,
                CENTER_DECEL_MS,
            )
            position_updated |= go_to_pos(axis, center_position, trigger=False)
        except Exception:
            logger.exception("Failed to center hub axis %s after fault", axis)

    if position_updated:
        try:
            motion_controller.planner_trigger(0, log_hex=False, check_crc=True)
        except Exception:
            logger.exception("Failed to trigger hub centering after fault")


def _hub_axes_accept_simhub_positions():
    now = time.monotonic()
    hubAxes = enabled_axes(HUB_AXIS_FROM, HUB_AXIS_TO)
    if not hubAxes:
        return True

    # Poll one hub axis at a time.  Every axis is still checked once per
    # HUB_STATUS_POLL_INTERVAL, but four reads no longer block one sender
    # cycle as a single burst.
    pollStep = HUB_STATUS_POLL_INTERVAL / len(hubAxes)
    if now - runtime_state.hub_fault_last_check < pollStep:
        return not runtime_state.hub_fault_active

    runtime_state.hub_fault_last_check = now
    axis = hubAxes[runtime_state.hub_status_index % len(hubAxes)]
    runtime_state.hub_status_index = (
        runtime_state.hub_status_index + 1
    ) % len(hubAxes)

    try:
        status = motion_controller.read_servo_status(axis)
        if status == 3:
            runtime_state.hub_fault_details[axis] = f"{axis}:fault"
        else:
            runtime_state.hub_fault_details.pop(axis, None)
    except Exception as e:
        runtime_state.hub_fault_details[axis] = f"{axis}:read error {e}"

    faultActive = bool(runtime_state.hub_fault_details)

    faultWasActive = runtime_state.hub_fault_active
    runtime_state.hub_fault_active = faultActive

    if faultActive != faultWasActive:
        if faultActive:
            detailText = ", ".join(
                runtime_state.hub_fault_details[axis]
                for axis in sorted(runtime_state.hub_fault_details)
            )
            logger.warning("Blocking SimHub positions for axes 4-7 (%s)", detailText)
            _center_hub_axes_after_fault(hubAxes)
        else:
            logger.info("Axes 4-7 OK; accepting SimHub positions again")

    return not runtime_state.hub_fault_active
def axis_enabled(axis:int):
    return 1 <= axis <= axisCount and bool(enableAxis[axis - 1])

def enabled_axes(firstAxis:int=1, lastAxis:int | None=None):
    if lastAxis is None:
        lastAxis = axisCount
    return [axis for axis in range(firstAxis, lastAxis + 1) if axis_enabled(axis)]

def handle_init():
    with runtime_state.position_lock:
        runtime_state.shutdown_active = False
    if runtime_state.initializing:
        raise RuntimeError("A6 initialization is already running")
    runtime_state.initializing = True
    runtime_state.initialized = False
    try:
        _run_initialization()
    except Exception:
        runtime_state.initialized = False
        try:
            motion_controller.disconnect()
        except Exception:
            logger.exception("Cleanup after failed initialization failed")
        raise
    else:
        runtime_state.initialized = True
    finally:
        runtime_state.initializing = False


def ensure_maintenance_initialized():
    """Prepare ModBus and axis parameters without homing or moving axes."""
    with runtime_state.position_lock:
        if motion_controller.connected:
            if runtime_state.initialized:
                runtime_state.maintenance_planner_axes.update(enabled_axes())
            return False
        logger.info("Preparing ModBus and A6 axes for maintenance")
        try:
            _prepare_axes_for_initialization()
        except Exception:
            try:
                motion_controller.disconnect()
            except Exception:
                logger.exception("Maintenance connection cleanup failed")
            raise
        logger.info("ModBus and A6 axes prepared for maintenance")
        return True


def _prepare_axes_for_initialization():
    if not motion_controller.connected:
        motion_controller.connect(axisCount)

    runtime_state.maintenance_planner_axes.clear()
    for axis in enabled_axes():
        motion_controller.initialize_parameters(axis)
        #time.sleep(0.1)
        motion_controller.planner_stop(axis) # Ensure that the planner is stopped
        #time.sleep(0.1)

    return enabled_axes()


def _run_initialization():
    logger.info("Initializing A6")

    initAxes = _prepare_axes_for_initialization()

    referenced_axes = []
    axes_to_home = []
    for axis in initAxes:
        if motion_controller.homing_completed(axis):
            referenced_axes.append(axis)
        else:
            axes_to_home.append(axis)

    if referenced_axes:
        logger.info("Axes already referenced: %s", referenced_axes)
    if axes_to_home:
        logger.info("Starting homing for unreferenced axes: %s", axes_to_home)
        for axis in axes_to_home:
            # The status was checked immediately above.  Do not let a second,
            # changing read suppress a homing command selected by that check.
            motion_controller.start_homing(
                axis,
                ignore_status=True,
                trigger=False,
            )
        time.sleep(1)
        # All selected axes are enabled and prepared before one broadcast edge
        # reaches all seven drives at the same time.
        motion_controller.trigger_homing(0)
        wait_for_homing(1, MAX_AXIS)
    else:
        logger.info("All enabled axes are already referenced")

    if Leveling.load_leveling_offsets():
        Leveling.apply_leveling_offsets()
    else:
        logger.info("No leveling offsets loaded")

    time.sleep(0.2)

    logger.info("Moving to lowest position")
    for axis in enabled_axes(4, 7):
        motion_controller.planner_start(axis)
        motion_controller.planner_set_parameters(axis, 50, 500, 500)
    axis_to_position(4, 7, 0)
    time.sleep(1.0)
    wait_for_axis_to_reach_position(4, 7)
    logger.info("Lowest position reached")
    for axis in enabled_axes(4, 7):
        motion_controller.planner_set_parameters(axis, 200, 300, 300)  # Set parameters for each axis

    time.sleep(0.2)

    for axis in enabled_axes(1, 3):
        motion_controller.planner_start(axis)

    if axes_to_home:
        logger.info("Moving to opposite position")
        all_axis_to_position(maxPos)
        logger.info("All axes reached opposite position")

    logger.info("Centering all axes")
    all_axis_to_position(int(maxPos/2))
    logger.info("All axes centered")

    for axis in enabled_axes():
        motion_controller.planner_set_parameters(axis, rpm_from_mm_per_second(axis, SPEED_MM_S[axis - 1]), 300, 300)  # Set parameters for each axis
        # time.sleep(0.1)

    logger.info("Initialization complete")

def dynamic_parameter_update_due(axis:int):
    now = time.monotonic()
    index = axis - 1
    nextUpdate = runtime_state.dynamic_parameter_next_update[index]

    if nextUpdate == 0.0:
        # Stagger the first update of axes 1-3 instead of writing all dynamic
        # parameters in the same sender cycle.
        nextUpdate = now + index * (DYNAMIC_PARAMETER_UPDATE_INTERVAL_S / 3.0)
        runtime_state.dynamic_parameter_next_update[index] = nextUpdate

    if now < nextUpdate:
        return False

    runtime_state.dynamic_parameter_next_update[index] = (
        now + DYNAMIC_PARAMETER_UPDATE_INTERVAL_S
    )
    return True

def handle_pos_2(axis:int, value:int, trigger:bool=True):
    with runtime_state.position_lock:
        return _handle_pos_2(axis, value, trigger)


def _handle_pos_2(axis:int, value:int, trigger:bool=True):
    if (
        not runtime_state.positions_enabled
        or runtime_state.maintenance_active
        or runtime_state.shutdown_active
        or not runtime_state.simhub_positions_armed
        or not runtime_state.initialized
        or runtime_state.initializing
        or Grease.greaseActive
        or Leveling.levelingActive
        or not axis_enabled(axis)
    ):
        return False
    if _axis_is_hub_axis(axis) and not _hub_axes_accept_simhub_positions():
        return False

    targetPos = target_position_mm(axis, value)
    if abs(runtime_state.previous_positions[axis - 1] - targetPos) <= 1.0:
        return False

    axisAccTime = ACC_TIME_MS[axis - 1]
    axisDecTime = DEC_TIME_MS[axis - 1]
    axisRpm = rpm_from_mm_per_second(axis, SPEED_MM_S[axis - 1])
    if not motion_controller.planner_parameters_match(
        axis, axisRpm, axisAccTime, axisDecTime
    ):
        motion_controller.planner_set_parameters(
            axis,
            axisRpm,
            axisAccTime,
            axisDecTime,
            queued=True,
        )

    positionUpdated = go_to_pos(
        axis, value, trigger, targetPos=targetPos, queued=True
    )
    if not positionUpdated:
        return False
    a6_simulator.set_target(
        axis,
        targetPos,
        axisAccTime,
        SPEED_MM_S[axis - 1],
        axisDecTime,
        raw_target=value,
    )
    runtime_state.previous_raw_positions[axis - 1] = value
    return True

def _CAN_BE_DELETED_handle_pos(axis:int, value:int):
    if (
        not runtime_state.positions_enabled
        or not runtime_state.initialized
        or runtime_state.initializing
        or Grease.greaseActive
        or Leveling.levelingActive
        or not axis_enabled(axis)
    ):
        return
    if _axis_is_hub_axis(axis) and not _hub_axes_accept_simhub_positions():
        return
    go_to_pos(axis,value)

def target_position_mm(axis:int, value:int):
    pos = maxStroke[axis - 1] / maxPos * value - maxStroke[axis - 1] / 2
    pos += zeroOffset[axis - 1]
    pos += levelingOffset[axis - 1]
    return pos

def go_to_pos(
    axis:int,
    value:int,
    trigger:bool=True,
    targetPos=None,
    queued:bool=False,
):
    if not axis_enabled(axis):
        return False
    pos = target_position_mm(axis, value) if targetPos is None else targetPos
    if abs(runtime_state.previous_positions[axis - 1] - pos) > 1.0:
        runtime_state.previous_positions[axis - 1] = pos
        motion_controller.planner_set_position_mm(
            axis,
            pos,
            log_hex=False,
            check_crc=True,
            trigger=trigger,
            queued=queued,
        )
        return True
    return False

def all_axis_to_position(position:int):
    axis_to_position(1, axisCount, position)

def axis_to_position(firstAxis:int, lastAxis:int, position:int):
    axes = enabled_axes(firstAxis, lastAxis)
    if not axes:
        return
    triggerIt = firstAxis == lastAxis
    for axis in axes:
        go_to_pos(axis,position, triggerIt)  # Trigger only if just one axis is being moved
        time.sleep(0.1)

    if (not triggerIt):
        motion_controller.planner_trigger(0, log_hex=False, check_crc=True)
    time.sleep(1)
    wait_for_axis_to_reach_position(firstAxis, lastAxis)

def wait_for_axis_to_reach_position(
    firstAxis: int,
    lastAxis: int,
    timeout: float = AXIS_WAIT_TIMEOUT_S,
):
    waitAxes = enabled_axes(firstAxis, lastAxis)
    if not waitAxes:
        return True
    deadline = time.monotonic() + timeout
    pending_axes = waitAxes
    while time.monotonic() < deadline:
        pending_axes = [
            axis
            for axis in waitAxes
            if not motion_controller.position_reached(axis, ignore_status=True)
        ]
        if not pending_axes:
            return True
        time.sleep(WAIT_POLL_INTERVAL_S)
    raise TimeoutError(
        f"Axes {pending_axes} did not reach their target within {timeout:.1f}s"
    )

def wait_for_homing(
    firstAxis: int,
    lastAxis: int,
    timeout: float = HOMING_WAIT_TIMEOUT_S,
):
    waitAxes = enabled_axes(firstAxis, lastAxis)
    if not waitAxes:
        return True
    logger.info("Waiting for homing")
    deadline = time.monotonic() + timeout
    pending_axes = waitAxes
    while time.monotonic() < deadline:
        pending_axes = [axis for axis in waitAxes if not motion_controller.homing_complete(axis)]
        if not pending_axes:
            logger.info("All axes homed")
            return True
        time.sleep(WAIT_POLL_INTERVAL_S)
    raise TimeoutError(
        f"Axes {pending_axes} did not finish homing within {timeout:.1f}s"
    )

def handle_end():
    logger.info("Shutting down A6")
    with runtime_state.position_lock:
        runtime_state.shutdown_active = True
        runtime_state.simhub_positions_armed = False
    if not runtime_state.initialized:
        motion_controller.disconnect()
        return

    try:
        logger.info("Centering all axes before shutdown")
        center_all_axes()
        logger.info("All axes centered")
        time.sleep(SHUTDOWN_CENTER_SETTLE_S)

        for axis in enabled_axes():
            #time.sleep(0.2)
            motion_controller.planner_stop(axis)

        hub_axes = enabled_axes(4, 7)
        for axis in hub_axes:
            # Prepare every hub axis before sending one shared start edge.
            motion_controller.start_homing(
                axis,
                ignore_status=True,
                initialize=False,
                trigger=False,
            )
        time.sleep(1)
        if hub_axes:
            motion_controller.trigger_homing(0)
        wait_for_homing(4, 7)
    finally:
        runtime_state.initialized = False
        motion_controller.disconnect()

def main():
    from .SimHub2SimRig import main as simhub_main

    simhub_main()

if __name__ == "__main__":
    main()
