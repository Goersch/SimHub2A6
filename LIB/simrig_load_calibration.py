"""Persistent static load calibration for the four SimRig hub drives."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import RIG_CONFIG
from .logging_config import get_logger

logger = get_logger("simrig.loads")
HUB_AXIS_IDS = (4, 5, 6, 7)
LOAD_CALIBRATION_PATH = (
    Path(__file__).resolve().parent.parent / "INI" / "simrig_load_values.json"
)


@dataclass(frozen=True)
class SimRigLoadCalibration:
    recorded_at: str
    load_rates: tuple[float, float, float, float]
    normalized_loads: tuple[float, float, float, float]
    center_of_gravity_front_to_rear_mm: float
    center_of_gravity_left_to_right_mm: float


def _hub_points():
    front_width = RIG_CONFIG.distance_front_drives_left_to_right_mm
    rear_width = RIG_CONFIG.distance_rear_drives_left_to_right_mm
    wheelbase = RIG_CONFIG.distance_front_to_rear_drives_mm
    return (
        (wheelbase / 2.0, -front_width / 2.0),
        (wheelbase / 2.0, front_width / 2.0),
        (-wheelbase / 2.0, rear_width / 2.0),
        (-wheelbase / 2.0, -rear_width / 2.0),
    )


def calibration_from_load_rates(load_rates, recorded_at=None):
    values = tuple(float(value) for value in load_rates)
    if len(values) != len(HUB_AXIS_IDS):
        raise ValueError("Load values are required for axes 4 through 7")
    if any(value <= 0.0 for value in values):
        raise ValueError("All four Load values must be greater than zero")

    total = sum(values)
    normalized = tuple(value / total for value in values)
    points = _hub_points()
    center_front_to_rear = sum(
        load * point[0] for load, point in zip(normalized, points, strict=False)
    )
    center_left_to_right = sum(
        load * point[1] for load, point in zip(normalized, points, strict=False)
    )
    return SimRigLoadCalibration(
        recorded_at or datetime.now().isoformat(timespec="seconds"),
        values,
        normalized,
        center_front_to_rear,
        center_left_to_right,
    )


def load_calibration(path=LOAD_CALIBRATION_PATH):
    calibration_path = Path(path)
    try:
        data = json.loads(calibration_path.read_text(encoding="utf-8"))
        load_rates = tuple(data["loadRates"][str(axis)] for axis in HUB_AXIS_IDS)
        return calibration_from_load_rates(load_rates, data.get("recordedAt"))
    except FileNotFoundError:
        return None
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
        logger.warning(
            "Stored SimRig Load values could not be loaded from %s: %s",
            calibration_path,
            error,
        )
        return None


_calibration_lock = threading.RLock()
_active_calibration = load_calibration()


def get_active_calibration():
    with _calibration_lock:
        return _active_calibration


def get_active_center_of_gravity():
    calibration = get_active_calibration()
    if calibration is None:
        return (
            RIG_CONFIG.center_of_gravity_front_to_rear_mm,
            RIG_CONFIG.center_of_gravity_left_to_right_mm,
        )
    return (
        calibration.center_of_gravity_front_to_rear_mm,
        calibration.center_of_gravity_left_to_right_mm,
    )


def save_load_calibration(load_rates, path=LOAD_CALIBRATION_PATH, *, activate=True):
    global _active_calibration

    calibration = calibration_from_load_rates(load_rates)
    calibration_path = Path(path)
    data = {
        "recordedAt": calibration.recorded_at,
        "loadRates": {
            str(axis): value
            for axis, value in zip(HUB_AXIS_IDS, calibration.load_rates, strict=False)
        },
        "normalizedLoads": {
            str(axis): value
            for axis, value in zip(HUB_AXIS_IDS, calibration.normalized_loads, strict=False)
        },
        "centerOfGravityFront2Rear": (
            calibration.center_of_gravity_front_to_rear_mm
        ),
        "centerOfGravityLeft2Right": (
            calibration.center_of_gravity_left_to_right_mm
        ),
    }
    calibration_path.parent.mkdir(parents=True, exist_ok=True)
    calibration_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if activate:
        with _calibration_lock:
            _active_calibration = calibration
    logger.info("Stored SimRig Load values in %s", calibration_path)
    return calibration
