"""Compact diagnostics for the latest SimHub motion recording.

The script intentionally reads only local configuration and CSV data and never
prints credential-bearing configuration values.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "SimHubData"
INI_PATH = ROOT / "INI" / "SimHub2SimRig.ini"
SIMHUB_PATH = Path(r"C:\Program Files (x86)\SimHub\PluginsData\Common\MotionPlugin.GeneralSettingsV2.json")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    index = (len(ordered) - 1) * fraction
    lo = int(math.floor(index))
    hi = int(math.ceil(index))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - index) + ordered[hi] * (index - lo)


def main() -> None:
    csv_path = max(CSV_DIR.glob("*.csv"), key=lambda path: path.stat().st_mtime)
    rows: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))

    timestamps = [datetime.fromisoformat(row["Timestamp"]) for row in rows]
    cycle_ms = [(b - a).total_seconds() * 1000 for a, b in zip(timestamps, timestamps[1:], strict=False)]
    result: dict[str, object] = {
        "recording": csv_path.name,
        "rows": len(rows),
        "duration_s": round((timestamps[-1] - timestamps[0]).total_seconds(), 3),
        "cycle_ms": {
            "median": round(median(cycle_ms), 3),
            "p95": round(percentile(cycle_ms, 0.95), 3),
            "max": round(max(cycle_ms), 3),
        },
        "axes": [],
    }

    speed_limits = {axis: (300.0 if axis <= 3 else 135.0) for axis in range(1, 8)}
    for axis in range(1, 8):
        target = [float(row[f"TargetPosition{axis}"]) for row in rows]
        model = [float(row[f"CalculatedActualPosition{axis}"]) for row in rows]
        actual = [float(row[f"ActualPosition{axis}"]) for row in rows]
        age = [float(row[f"ActualPositionAgeMs{axis}"]) for row in rows]
        velocity = [float(row[f"ActualVelocity{axis}"]) for row in rows]
        steps = [abs(b - a) for a, b in zip(target, target[1:], strict=False)]
        changed_steps = [step for step in steps if step > 1e-6]
        changes = [index for index, step in enumerate(steps, start=1) if step > 1e-6]
        gaps = [
            (timestamps[b] - timestamps[a]).total_seconds() * 1000
            for a, b in zip(changes, changes[1:], strict=False)
        ]
        directions = []
        for a, b in zip(target, target[1:], strict=False):
            delta = b - a
            if abs(delta) > 1e-6:
                directions.append(1 if delta > 0 else -1)
        reversals = sum(a != b for a, b in zip(directions, directions[1:], strict=False))
        model_target = [abs(a - b) for a, b in zip(model, target, strict=False)]
        actual_model = [abs(a - b) for a, b in zip(actual, model, strict=False)]
        result["axes"].append(
            {
                "axis": axis,
                "target_min_mm": round(min(target), 3),
                "target_max_mm": round(max(target), 3),
                "target_utilization_of_95pct_stroke": round(max(abs(min(target)), abs(max(target))) / 95 * 100, 1),
                "target_changes": len(changes),
                "target_change_gap_median_ms": round(median(gaps), 1) if gaps else None,
                "target_step_p95_mm": round(percentile(changed_steps, 0.95), 3),
                "target_step_max_mm": round(max(changed_steps), 3),
                "direction_reversals": reversals,
                "model_target_mae_mm": round(sum(model_target) / len(model_target), 3),
                "actual_model_mae_all_rows_mm": round(sum(actual_model) / len(actual_model), 3),
                "velocity_p95_mm_s": round(percentile([abs(value) for value in velocity], 0.95), 1),
                "velocity_max_mm_s": round(max(abs(value) for value in velocity), 1),
                "velocity_limit_mm_s": speed_limits[axis],
                "velocity_near_limit_pct": round(sum(abs(value) >= speed_limits[axis] * 0.98 for value in velocity) / len(velocity) * 100, 2),
                "read_age_median_ms": round(median(age), 1),
                "read_age_p95_ms": round(percentile(age, 0.95), 1),
                "read_age_max_ms": round(max(age), 1),
            }
        )

    ground: dict[str, object] = {}
    for axis in range(4, 8):
        deviations = [float(row[f"GroundDeviation{axis}"]) for row in rows]
        loads = [float(row[f"GroundLoad{axis}"]) for row in rows]
        ground[str(axis)] = {
            "deviation_min_mm": round(min(deviations), 4),
            "deviation_max_mm": round(max(deviations), 4),
            "deviation_abs_p95_mm": round(percentile([abs(value) for value in deviations], 0.95), 4),
            "load_min_pct": round(min(loads) * 100, 3),
            "load_max_pct": round(max(loads) * 100, 3),
            "zero_load_rows_pct": round(sum(value <= 1e-9 for value in loads) / len(loads) * 100, 3),
        }
    result["ground"] = ground
    result["source_mtimes"] = {
        "simhub": datetime.fromtimestamp(SIMHUB_PATH.stat().st_mtime).isoformat(timespec="seconds"),
        "recording": datetime.fromtimestamp(csv_path.stat().st_mtime).isoformat(timespec="seconds"),
        "local_ini": datetime.fromtimestamp(INI_PATH.stat().st_mtime).isoformat(timespec="seconds"),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
