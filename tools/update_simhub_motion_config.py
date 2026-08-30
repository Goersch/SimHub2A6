"""Safely update selected values in SimHub's active motion configuration.

By default the script only displays the planned changes. Pass ``--apply`` to
create a timestamped backup and atomically replace the SimHub JSON file.
SimHub must be completely stopped while applying changes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_CONFIG = Path(
    r"C:\Program Files (x86)\SimHub\PluginsData\Common\MotionPlugin.GeneralSettingsV2.json"
)


class ConfigurationError(RuntimeError):
    """Raised when the expected active SimHub configuration is not found."""


def _active_device(document: dict) -> dict:
    devices = document.get("Devices") or []
    selected_id = document.get("SelectedDeviceId")
    selected = next(
        (device for device in devices if device.get("DeviceId") == selected_id),
        None,
    )
    if selected is not None:
        return selected

    active = [device for device in devices if device.get("IsActive")]
    if len(active) == 1:
        return active[0]
    raise ConfigurationError("The active SimHub motion device could not be identified uniquely.")


def _active_profile(device: dict) -> dict:
    profiles = device.get("Profiles") or []
    active_id = device.get("activeProfileId")
    profile = next(
        (item for item in profiles if item.get("ProfileId") == active_id),
        None,
    )
    if profile is None:
        raise ConfigurationError("The active motion profile was not found.")
    return profile


def _effect(profile: dict, type_name: str) -> dict:
    matches = [
        item
        for item in profile.get("Effects") or []
        if item.get("TypeName") == type_name
    ]
    if len(matches) != 1:
        raise ConfigurationError(
            f"Effect {type_name!r} was not found uniquely ({len(matches)} matches)."
        )
    return matches[0]


def _udp_output(device: dict) -> dict:
    outputs = (
        device.get("OutputEx", {}).get("AggregatedOutputs", [])
    )
    matches = [
        item["Output"]
        for item in outputs
        if item.get("IsEnabled")
        and item.get("Output", {}).get("TypeName") == "GenericUDPOutput"
    ]
    if len(matches) != 1:
        raise ConfigurationError(
            "The active GenericUDPOutput was not found uniquely "
            f"({len(matches)} matches)."
        )
    return matches[0]


def _set_value(changes: list[tuple[str, object, object]], target: dict,
               key: str, value: object, label: str) -> None:
    if key not in target:
        raise ConfigurationError(f"Erwarteter Parameter fehlt: {label}")
    previous = target[key]
    if previous != value:
        changes.append((label, previous, value))
        target[key] = value


def update_document(document: dict, *, smoothing: float,
                    udp_delay_ms: int, platform_speed_mm_s: float):
    device = _active_device(document)
    profile = _active_profile(device)
    changes: list[tuple[str, object, object]] = []

    effect_settings = _effect(
        profile, "ExtraAxisFrontRearTractionLossEffect"
    ).get("Settings", {})
    _set_value(
        changes,
        effect_settings,
        "Smoothing",
        smoothing,
        "Front/Rear Traction Loss: Smoothing",
    )

    protocol = (
        _udp_output(device)
        .get("Settings", {})
        .get("GenericProtocolDefinition", {})
    )
    update_command = protocol.get("UpdateCommand", {})
    _set_value(
        changes,
        update_command,
        "CommandDelay",
        udp_delay_ms,
        "Generic UDP: UpdateCommand.CommandDelay",
    )

    geometry_settings = (
        device.get("Geometry", {})
        .get("BaseGeometry", {})
        .get("Settings", {})
    )
    _set_value(
        changes,
        geometry_settings,
        "ActuatorsSpikeFilterMmPerSecond",
        platform_speed_mm_s,
        "4-lift geometry: ActuatorsSpikeFilterMmPerSecond",
    )
    return device, profile, changes


def _write_atomically(path: Path, document: dict, *, keep_bom: bool) -> None:
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    encoding = "utf-8-sig" if keep_bom else "utf-8"
    payload = text.encode(encoding)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update three values in the active SimHub motion profile."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--smoothing", type=float, default=15.0)
    parser.add_argument("--udp-delay-ms", type=int, default=10)
    parser.add_argument("--platform-speed-mm-s", type=float, default=135.0)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create a backup and apply the changes.",
    )
    arguments = parser.parse_args()

    path = arguments.config.resolve()
    raw = path.read_bytes()
    keep_bom = raw.startswith(b"\xef\xbb\xbf")
    document = json.loads(raw.decode("utf-8-sig"))
    device, profile, changes = update_document(
        document,
        smoothing=arguments.smoothing,
        udp_delay_ms=arguments.udp_delay_ms,
        platform_speed_mm_s=arguments.platform_speed_mm_s,
    )

    game = device.get("CurrentProfileGame", {}).get("CurrentGame", "unknown")
    print(f"Device: {device.get('Name', 'unknown')}")
    print(f"Profile: {profile.get('Name', 'unknown')} / game: {game}")
    if not changes:
        print("No changes required; all target values are already set.")
        return 0
    for label, previous, value in changes:
        print(f"- {label}: {previous} -> {value}")

    if not arguments.apply:
        print("\nPreview only. Exit SimHub and run again with --apply to save.")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.backup-{timestamp}")
    shutil.copy2(path, backup)
    try:
        _write_atomically(path, document, keep_bom=keep_bom)
    except Exception:
        shutil.copy2(backup, path)
        raise
    print(f"\nBackup: {backup}")
    print(f"Updated: {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PermissionError as error:
        raise SystemExit(
            "Write permission denied. Start the terminal as Administrator.\n"
            f"Details: {error}"
        ) from error
    except (ConfigurationError, FileNotFoundError, json.JSONDecodeError) as error:
        raise SystemExit(f"Aborted: {error}") from error
