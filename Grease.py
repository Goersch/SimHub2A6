import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from .LIB.a6_motion_controller import motion_controller
from .LIB.config import GREASE_CONFIG
from .LIB.language import text as language_text
from .LIB.logging_config import get_logger

logger = get_logger("grease")


GREASE_RPM = GREASE_CONFIG.rpm
GREASE_ACCEL_MS = GREASE_CONFIG.accel_ms
GREASE_DECEL_MS = GREASE_CONFIG.decel_ms
GREASE_NEGATIVE_MM = GREASE_CONFIG.negative_mm
GREASE_POSITIVE_MM = GREASE_CONFIG.positive_mm
GREASE_CENTER_MM = GREASE_CONFIG.center_mm
GREASE_MOVE_TIMEOUT_S = GREASE_CONFIG.move_timeout_s
GREASE_POSITION_POLL_INTERVAL_S = GREASE_CONFIG.position_poll_interval_s
GREASE_ROWS = (
    (1, 1, language_text("Common", "axis_front")),
    (2, 2, language_text("Common", "axis_middle")),
    (3, 3, language_text("Common", "axis_rear")),
    (4, 7, language_text("Common", "axis_hub")),
)
GREASE_DATA_FILE = Path(__file__).resolve().parent / "INI" / "grease_data.json"

greaseActive = False
greaseAxisFrom: int | None = None
greaseAxisTo: int | None = None
greaseStopEvent: threading.Event | None = None
greaseThread: threading.Thread | None = None
greaseLock = threading.Lock()
greaseLastError: BaseException | None = None

_data_lock = threading.Lock()
_grease_data: dict[str, dict] | None = None
_last_playtime_update = datetime.now()
_last_playtime_save = _last_playtime_update
_was_simhub_playing = False

prevPos: list[float] = []
zeroOffset: list[float] = []


def _state_not_initialized(*_args):
    raise RuntimeError("Grease state is not initialized")


enabled_axes: Callable[[int, int | None], list[int]] = _state_not_initialized


class _CommandsState(Protocol):
    prevPos: list[float]
    zeroOffset: list[float]
    enabled_axes: Callable[[int, int | None], list[int]]


def _sync_command_state():
    global prevPos
    global zeroOffset
    global enabled_axes

    from . import SimHubCommands as commands

    commands_state = cast(_CommandsState, commands)
    prevPos = commands_state.prevPos
    zeroOffset = commands_state.zeroOffset
    enabled_axes = commands_state.enabled_axes


def _set_active(axisFrom=None, axisTo=None, active=False):
    global greaseActive
    global greaseAxisFrom
    global greaseAxisTo

    greaseActive = active
    greaseAxisFrom = axisFrom if active else None
    greaseAxisTo = axisTo if active else None


def grease_key(axis_from, axis_to):
    return str(axis_from) if axis_from == axis_to else f"{axis_from}-{axis_to}"


def _default_grease_entry():
    return {"lastGreaseAt": "", "playtimeMinutes": 0.0}


def _ensure_grease_entry(data, key):
    entry = data.get(key)
    if not isinstance(entry, dict):
        entry = _default_grease_entry()
        data[key] = entry
    entry.setdefault("lastGreaseAt", "")
    try:
        entry["playtimeMinutes"] = float(entry.get("playtimeMinutes", 0.0))
    except (TypeError, ValueError):
        entry["playtimeMinutes"] = 0.0
    return entry


def _load_grease_data_locked():
    global _grease_data
    if _grease_data is not None:
        return _grease_data
    try:
        data = json.loads(GREASE_DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for axis_from, axis_to, _ in GREASE_ROWS:
        _ensure_grease_entry(data, grease_key(axis_from, axis_to))
    _grease_data = data
    return data


def grease_data_snapshot():
    with _data_lock:
        data = _load_grease_data_locked()
        return {
            key: dict(_ensure_grease_entry(data, key))
            for key in data
        }


def save_grease_data():
    with _data_lock:
        data = _load_grease_data_locked()
        try:
            GREASE_DATA_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Grease data could not be saved: %s", exc)


def reset_grease_data(axis_from, axis_to):
    with _data_lock:
        data = _load_grease_data_locked()
        entry = _ensure_grease_entry(data, grease_key(axis_from, axis_to))
        entry["lastGreaseAt"] = datetime.now().isoformat(timespec="seconds")
        entry["playtimeMinutes"] = 0.0
    save_grease_data()


def update_playtime(simhub_playing: bool):
    """Accumulate operating time even while the maintenance dialog is closed."""
    global _last_playtime_update
    global _last_playtime_save
    global _was_simhub_playing

    now = datetime.now()
    elapsed_minutes = (now - _last_playtime_update).total_seconds() / 60.0
    should_save = False
    if simhub_playing and elapsed_minutes > 0:
        _sync_command_state()
        with _data_lock:
            data = _load_grease_data_locked()
            for axis_from, axis_to, _ in GREASE_ROWS:
                if not enabled_axes(axis_from, axis_to):
                    continue
                entry = _ensure_grease_entry(
                    data, grease_key(axis_from, axis_to)
                )
                entry["playtimeMinutes"] += elapsed_minutes
        should_save = (now - _last_playtime_save).total_seconds() >= 60
    elif _was_simhub_playing:
        should_save = True

    _last_playtime_update = now
    _was_simhub_playing = simhub_playing
    if should_save:
        save_grease_data()
        _last_playtime_save = now


def _set_parameters(axes):
    for axis in axes:
        motion_controller.planner_set_parameters(axis, GREASE_RPM, GREASE_ACCEL_MS, GREASE_DECEL_MS)


def _move_axes_to_mm(
    axes,
    targetMM,
    stopEvent=None,
    waitForStop=True,
    timeout=GREASE_MOVE_TIMEOUT_S,
):
    for axis in axes:
        targetPosition = zeroOffset[axis - 1] + targetMM
        prevPos[axis - 1] = targetPosition
        motion_controller.planner_set_position_mm(
            axis,
            targetPosition,
            log_hex=False,
            check_crc=True,
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stopEvent is not None and stopEvent.is_set() and not waitForStop:
            return False

        reached = 0
        for axis in axes:
            try:
                if motion_controller.position_reached(axis, ignore_status=True):
                    reached += 1
            except Exception as error:
                logger.warning("Axis %s position check failed: %s", axis, error)

        if reached == len(axes):
            return True

        time.sleep(GREASE_POSITION_POLL_INTERVAL_S)

    raise TimeoutError(
        f"Grease axes {axes} did not reach {targetMM:.2f}mm within {timeout:.1f}s"
    )


def _worker(axisFrom, axisTo, stopEvent):
    global greaseLastError
    axes = enabled_axes(axisFrom, axisTo)
    try:
        if not axes:
            logger.warning("No enabled axes for %s-%s", axisFrom, axisTo)
            return

        from . import SimHubCommands as commands

        commands.ensure_maintenance_planners(axes)
        for axis in axes:
            motion_controller.set_servo_enabled(axis, True)
        _set_parameters(axes)
        target = GREASE_NEGATIVE_MM
        while not stopEvent.is_set():
            _move_axes_to_mm(axes, target, stopEvent, waitForStop=False)
            target = (
                GREASE_POSITIVE_MM
                if target == GREASE_NEGATIVE_MM
                else GREASE_NEGATIVE_MM
            )

        _move_axes_to_mm(axes, GREASE_CENTER_MM, waitForStop=True)
        logger.info("Axes %s-%s centered", axisFrom, axisTo)
    except Exception as error:
        greaseLastError = error
        logger.error("Grease movement failed: %s", error)
    finally:
        if axes == list(range(4, 8)):
            for axis in axes:
                try:
                    motion_controller.set_servo_enabled(axis, False)
                except Exception:
                    logger.exception(
                        "Failed to apply hub brake after grease on axis %s",
                        axis,
                    )
        with greaseLock:
            global greaseThread
            global greaseStopEvent
            _set_active(active=False)
            greaseThread = None
            greaseStopEvent = None


def grease(axisFrom: int, axisTo: int, status: int):
    global greaseStopEvent
    global greaseThread
    global greaseLastError

    _sync_command_state()
    with greaseLock:
        if status:
            if greaseActive:
                return

            greaseLastError = None
            greaseStopEvent = threading.Event()
            _set_active(axisFrom, axisTo, True)
            greaseThread = threading.Thread(
                target=_worker,
                args=(axisFrom, axisTo, greaseStopEvent),
                daemon=True,
            )
            greaseThread.start()
            logger.info("Start axes %s-%s", axisFrom, axisTo)
        else:
            if not greaseActive:
                return

            logger.info("Stop axes %s-%s", axisFrom, axisTo)
            if greaseStopEvent is not None:
                greaseStopEvent.set()
