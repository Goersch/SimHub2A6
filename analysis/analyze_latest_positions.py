"""Reproducible diagnostics for the latest SimHub position recording."""

from __future__ import annotations

import csv
import json
import math
import statistics
from bisect import bisect_left
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = max((ROOT / "SimHubData").glob("*.csv"), key=lambda path: path.stat().st_mtime)


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def error_stats(values):
    if not values:
        return {"count": 0}
    absolute = [abs(value) for value in values]
    return {
        "count": len(values),
        "bias_mm": statistics.fmean(values),
        "mae_mm": statistics.fmean(absolute),
        "rmse_mm": math.sqrt(statistics.fmean(value * value for value in values)),
        "p95_abs_mm": percentile(absolute, 0.95),
        "max_abs_mm": max(absolute),
    }


rows = []
with SOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
    for row in csv.DictReader(handle, delimiter=";"):
        timestamp = datetime.fromisoformat(row["Timestamp"]).timestamp()
        actual_read_timestamps = []
        actual_ages_ms = []
        for axis in range(1, 8):
            raw_read_timestamp = row.get(f"ActualPositionTimestamp{axis}")
            raw_age_ms = row.get(f"ActualPositionAgeMs{axis}")
            try:
                read_timestamp = (
                    datetime.fromisoformat(raw_read_timestamp).timestamp()
                    if raw_read_timestamp is not None
                    else None
                )
            except (TypeError, ValueError):
                read_timestamp = None
            try:
                age_ms = float(raw_age_ms) if raw_age_ms is not None else None
            except (TypeError, ValueError):
                age_ms = None
            if read_timestamp is None and age_ms is not None:
                read_timestamp = timestamp - age_ms / 1000.0
            actual_read_timestamps.append(read_timestamp)
            actual_ages_ms.append(age_ms)
        rows.append(
            {
                "timestamp": timestamp,
                "target": [float(row[f"TargetPosition{axis}"]) for axis in range(1, 8)],
                "calculated": [
                    float(row[f"CalculatedActualPosition{axis}"])
                    for axis in range(1, 8)
                ],
                "actual": [float(row[f"ActualPosition{axis}"]) for axis in range(1, 8)],
                "actual_read_timestamp": actual_read_timestamps,
                "actual_age_ms": actual_ages_ms,
                "velocity": [float(row[f"ActualVelocity{axis}"]) for axis in range(1, 8)],
            }
        )

started = rows[0]["timestamp"]
ended = rows[-1]["timestamp"]
timestamps = [row["timestamp"] for row in rows]
intervals_ms = [
    (current["timestamp"] - previous["timestamp"]) * 1000.0
    for previous, current in zip(rows, rows[1:], strict=False)
]
timestamped_actual_positions = any(
    any(value is not None for value in row["actual_read_timestamp"])
    for row in rows
)
first_actual_index = next(
    (
        index
        for index, row in enumerate(rows)
        if (
            any(value is not None for value in row["actual_read_timestamp"])
            if timestamped_actual_positions
            else any(abs(value) > 0.0005 for value in row["actual"])
        )
    ),
    None,
)
analysis_from = (
    rows[first_actual_index]["timestamp"] + 1.0
    if first_actual_index is not None
    else ended
)

axis_results = []
for axis_index in range(7):
    eligible = [row for row in rows if row["timestamp"] >= analysis_from]
    moving = [
        row
        for row in eligible
        if abs(row["velocity"][axis_index]) >= 1.0
        or abs(row["target"][axis_index] - row["calculated"][axis_index]) >= 0.1
    ]
    model_errors = [
        row["calculated"][axis_index] - row["target"][axis_index]
        for row in eligible
    ]
    actual_target_errors = [
        row["actual"][axis_index] - row["target"][axis_index]
        for row in eligible
    ]
    actual_calculated_errors = [
        row["actual"][axis_index] - row["calculated"][axis_index]
        for row in eligible
    ]
    moving_model_errors = [
        row["calculated"][axis_index] - row["target"][axis_index]
        for row in moving
    ]
    moving_actual_errors = [
        row["actual"][axis_index] - row["target"][axis_index]
        for row in moving
    ]
    moving_actual_calculated = [
        row["actual"][axis_index] - row["calculated"][axis_index]
        for row in moving
    ]
    settled_actual_calculated = []
    settled_actual_target = []
    last_model_motion = analysis_from
    for row in eligible:
        model_is_moving = (
            abs(row["velocity"][axis_index]) >= 0.1
            or abs(
                row["target"][axis_index] - row["calculated"][axis_index]
            ) >= 0.05
        )
        if model_is_moving:
            last_model_motion = row["timestamp"]
        elif row["timestamp"] - last_model_motion >= 1.2:
            settled_actual_calculated.append(
                row["actual"][axis_index] - row["calculated"][axis_index]
            )
            settled_actual_target.append(
                row["actual"][axis_index] - row["target"][axis_index]
            )

    change_indexes = []
    event_timestamps = []
    if timestamped_actual_positions:
        previous_read_timestamp = None
        for index, row in enumerate(rows):
            read_timestamp = row["actual_read_timestamp"][axis_index]
            if read_timestamp is None or read_timestamp < analysis_from:
                continue
            if read_timestamp != previous_read_timestamp:
                change_indexes.append(index)
                event_timestamps.append(read_timestamp)
                previous_read_timestamp = read_timestamp
    else:
        previous_value = None
        for index, row in enumerate(rows):
            if row["timestamp"] < analysis_from:
                continue
            value = row["actual"][axis_index]
            if previous_value is not None and value != previous_value:
                change_indexes.append(index)
                event_timestamps.append(row["timestamp"])
            previous_value = value
    event_comparison_indexes = [
        min(max(bisect_left(timestamps, event_timestamp), 0), len(rows) - 1)
        for event_timestamp in event_timestamps
    ]
    change_gaps = [
        current - previous
        for previous, current in zip(event_timestamps, event_timestamps[1:], strict=False)
    ]
    change_errors = [
        rows[event_index]["actual"][axis_index]
        - rows[comparison_index]["calculated"][axis_index]
        for event_index, comparison_index in zip(
            change_indexes, event_comparison_indexes, strict=False
        )
    ]
    change_target_errors = [
        rows[event_index]["actual"][axis_index]
        - rows[comparison_index]["target"][axis_index]
        for event_index, comparison_index in zip(
            change_indexes, event_comparison_indexes, strict=False
        )
    ]
    change_model_errors = [
        rows[comparison_index]["calculated"][axis_index]
        - rows[comparison_index]["target"][axis_index]
        for comparison_index in event_comparison_indexes
    ]
    largest_change_errors = []
    event_pairs = list(zip(change_indexes, event_comparison_indexes, strict=False))
    for event_index, comparison_index in sorted(
        event_pairs,
        key=lambda indexes: abs(
            rows[indexes[0]]["actual"][axis_index]
            - rows[indexes[1]]["calculated"][axis_index]
        ),
        reverse=True,
    )[:3]:
        row = rows[event_index]
        comparison_row = rows[comparison_index]
        largest_change_errors.append(
            {
                "time_s": event_timestamps[change_indexes.index(event_index)] - started,
                "target_mm": comparison_row["target"][axis_index],
                "calculated_mm": comparison_row["calculated"][axis_index],
                "actual_mm": row["actual"][axis_index],
                "actual_minus_calculated_mm": (
                    row["actual"][axis_index]
                    - comparison_row["calculated"][axis_index]
                ),
                "calculated_velocity_mm_s": comparison_row["velocity"][axis_index],
            }
        )

    dynamic_event_pairs = [
        (event_index, comparison_index, event_timestamp)
        for event_index, comparison_index, event_timestamp in zip(
            change_indexes, event_comparison_indexes, event_timestamps, strict=False
        )
        if abs(rows[comparison_index]["velocity"][axis_index]) >= 5.0
        or abs(
            rows[comparison_index]["target"][axis_index]
            - rows[comparison_index]["calculated"][axis_index]
        ) >= 0.5
    ]
    lag_candidates = []
    for lag_step in range(0, 101):
        lag_s = lag_step * 0.015
        errors = []
        for event_index, _comparison_index, event_timestamp in dynamic_event_pairs:
            comparison_time = event_timestamp - lag_s
            comparison_index = bisect_left(timestamps, comparison_time)
            comparison_index = min(max(comparison_index, 0), len(rows) - 1)
            errors.append(
                rows[event_index]["actual"][axis_index]
                - rows[comparison_index]["calculated"][axis_index]
            )
        if errors:
            lag_candidates.append((statistics.fmean(map(abs, errors)), lag_s))
    best_lag = min(lag_candidates) if lag_candidates else (None, None)

    max_error_index = max(
        range(len(eligible)),
        key=lambda index: abs(actual_target_errors[index]),
        default=None,
    )
    max_error_context = None
    if max_error_index is not None:
        row = eligible[max_error_index]
        max_error_context = {
            "time_s": row["timestamp"] - started,
            "target_mm": row["target"][axis_index],
            "calculated_mm": row["calculated"][axis_index],
            "actual_mm": row["actual"][axis_index],
        }

    axis_results.append(
        {
            "axis": axis_index + 1,
            "range_target_mm": [
                min(row["target"][axis_index] for row in eligible),
                max(row["target"][axis_index] for row in eligible),
            ],
            "model_minus_target": error_stats(model_errors),
            "actual_minus_target": error_stats(actual_target_errors),
            "actual_minus_calculated": error_stats(actual_calculated_errors),
            "moving_model_minus_target": error_stats(moving_model_errors),
            "moving_actual_minus_target": error_stats(moving_actual_errors),
            "moving_actual_minus_calculated": error_stats(moving_actual_calculated),
            "settled_actual_minus_calculated": error_stats(
                settled_actual_calculated
            ),
            "settled_actual_minus_target": error_stats(settled_actual_target),
            "moving_rows": len(moving),
            "actual_value_changes": len(change_indexes),
            "actual_event_source": (
                "read_timestamp" if timestamped_actual_positions else "value_change"
            ),
            "median_visible_update_gap_s": (
                statistics.median(change_gaps) if change_gaps else None
            ),
            "p95_visible_update_gap_s": percentile(change_gaps, 0.95),
            "error_at_actual_changes": error_stats(change_errors),
            "actual_target_error_at_changes": error_stats(change_target_errors),
            "model_target_error_at_actual_changes": error_stats(change_model_errors),
            "largest_actual_change_errors": largest_change_errors,
            "best_calculated_lag_s": best_lag[1],
            "best_lag_mae_mm": best_lag[0],
            "dynamic_zero_lag_mae_mm": (
                lag_candidates[0][0] if lag_candidates else None
            ),
            "dynamic_actual_changes": len(dynamic_event_pairs),
            "max_actual_target_context": max_error_context,
        }
    )

result = {
    "source": SOURCE.name,
    "rows": len(rows),
    "duration_s": ended - started,
    "started": datetime.fromtimestamp(started).isoformat(timespec="milliseconds"),
    "ended": datetime.fromtimestamp(ended).isoformat(timespec="milliseconds"),
    "interval_ms": {
        "median": statistics.median(intervals_ms),
        "p95": percentile(intervals_ms, 0.95),
        "max": max(intervals_ms),
    },
    "first_nonzero_actual_s": (
        rows[first_actual_index]["timestamp"] - started
        if first_actual_index is not None
        else None
    ),
    "analysis_warmup_s": analysis_from - started,
    "actual_event_source": (
        "read_timestamp" if timestamped_actual_positions else "value_change"
    ),
    "axes": axis_results,
}
print(json.dumps(result, indent=2))
