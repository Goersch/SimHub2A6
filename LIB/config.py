"""Typed application configuration loaded from ``INI/SimHub2SimRig.ini``."""

from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass, replace
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "INI" / "SimHub2SimRig.ini"


class ConfigurationError(ValueError):
    """The application INI file is missing or contains inconsistent values."""


@dataclass(frozen=True)
class ModbusConnectionConfig:
    port: str
    axes: frozenset[int]


@dataclass(frozen=True)
class ModbusConfig:
    connections: tuple[ModbusConnectionConfig, ...]
    baud: int
    serial_timeout_s: float
    inter_frame_delay_s: float
    response_poll_delay_s: float
    queue_drain_timeout_s: float
    task_wait_timeout_s: float
    worker_join_timeout_s: float


@dataclass(frozen=True)
class AxisConfig:
    axis_id: int
    enabled: bool
    spindle_pitch_mm: float
    stroke_mm: float
    zero_offset_mm: float
    speed_mm_s: float
    acc_time_ms: int
    dec_time_ms: int


@dataclass(frozen=True)
class MotionLimits:
    minimum_mm: float
    maximum_mm: float
    homing_mm: float
    reference_spindle_pitch_mm: float
    reference_app_units_per_mm: int
    raw_position_max: int


@dataclass(frozen=True)
class RigConfig:
    axes: tuple[AxisConfig, ...]
    limits: MotionLimits
    distance_front_drives_left_to_right_mm: float
    distance_rear_drives_left_to_right_mm: float
    distance_front_to_rear_drives_mm: float
    center_of_gravity_front_to_rear_mm: float
    center_of_gravity_left_to_right_mm: float
    center_of_gravity_height_mm: float

    @property
    def axis_count(self) -> int:
        return sum(axis.enabled for axis in self.axes)

    def axis(self, axis_id: int) -> AxisConfig:
        if not 1 <= axis_id <= len(self.axes):
            raise ValueError(f"Invalid axis: {axis_id}")
        return self.axes[axis_id - 1]


@dataclass(frozen=True)
class A6Config:
    poll_interval_s: float
    homing_start_observation_s: float
    operation_timeout_s: float
    invert_digital_inputs: bool
    homing_initial_rpm: int
    homing_end_rpm: int
    homing_accel_ms: int
    homing_decel_ms: int
    planner_start_rpm: int
    planner_start_accel_ms: int
    planner_start_decel_ms: int
    move_rpm: int
    move_accel_ms: int
    move_decel_ms: int


@dataclass(frozen=True)
class SimHubConfig:
    udp_ip: str
    udp_port: int
    disconnect_timeout_s: float
    sender_cycle_s: float
    position_count: int
    position_min: int
    position_max: int
    data_retention_days: int


@dataclass(frozen=True)
class ControlConfig:
    hub_axis_from: int
    hub_axis_to: int
    hub_status_poll_interval_s: float
    actual_position_poll_interval_s: float
    dynamic_accel_step_ms: int
    dynamic_parameter_update_interval_s: float
    center_rpm: int
    center_accel_ms: int
    center_decel_ms: int
    maintenance_middle_rpm: int
    maintenance_hub_rpm: int
    axis_wait_timeout_s: float
    homing_wait_timeout_s: float
    wait_poll_interval_s: float


@dataclass(frozen=True)
class LevelingStageConfig:
    name: str
    step_mm: float
    max_iterations: int
    target_tolerance: float
    kp: float
    allow_lowering: bool
    filter_alpha: float
    lower_step_factor: float
    raw_weight: float
    integral_gain: float


@dataclass(frozen=True)
class LevelingConfig:
    load_rate_units_per_percent: float
    load_bias_by_axis: dict[int, float]
    load_rate_samples: int
    load_rate_sample_delay_s: float
    settle_time_s: float
    settle_checks: int
    settle_check_delay_s: float
    settle_load_delta: float
    verify_batches: int
    verify_corrections: int
    minimum_tolerance: float
    offset_axes: tuple[int, ...]
    stages: tuple[LevelingStageConfig, ...]


@dataclass(frozen=True)
class GreaseConfig:
    rpm: int
    accel_ms: int
    decel_ms: int
    negative_mm: float
    positive_mm: float
    center_mm: float
    move_timeout_s: float
    position_poll_interval_s: float
    warning_after_operating_hours: float
    alarm_after_operating_hours: float


@dataclass(frozen=True)
class UiConfig:
    refresh_ms: int
    axis_status_refresh_ms: int
    trigger_chart_refresh_ms: int
    trigger_chart_window_s: float
    trigger_chart_max_ms: float
    disconnected_background: str
    connected_background: str


@dataclass(frozen=True)
class RoomLightConfig:
    uri: str
    request_timeout_s: float


@dataclass(frozen=True)
class ApplicationConfig:
    modbus: ModbusConfig
    rig: RigConfig
    a6: A6Config
    simhub: SimHubConfig
    control: ControlConfig
    leveling: LevelingConfig
    grease: GreaseConfig
    ui: UiConfig
    room_light: RoomLightConfig


def _csv_ints(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ConfigurationError(f"Invalid integer list: {value!r}") from exc


def _axis_float_map(value: str) -> dict[int, float]:
    result: dict[int, float] = {}
    if not value.strip():
        return result
    try:
        for item in value.split(","):
            axis, number = item.split(":", 1)
            result[int(axis.strip())] = float(number.strip())
    except ValueError as exc:
        raise ConfigurationError(f"Invalid axis:value list: {value!r}") from exc
    return result


def _positive(name: str, value: float) -> float:
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _axis_parameter_sections(
    parser: ConfigParser, maximum_axes: int
) -> dict[int, str]:
    """Resolve individual and grouped INI sections for every configured axis."""

    sections: dict[int, str] = {}
    for section in parser.sections():
        if not section.startswith("axes_"):
            continue
        for axis_id in _csv_ints(parser.get(section, "axes")):
            if not 1 <= axis_id <= maximum_axes:
                raise ConfigurationError(
                    f"Invalid axis {axis_id} in section [{section}]"
                )
            if axis_id in sections:
                raise ConfigurationError(
                    f"Axis {axis_id} occurs in multiple parameter groups"
                )
            sections[axis_id] = section

    for axis_id in range(1, maximum_axes + 1):
        individual_section = f"axis_{axis_id}"
        if parser.has_section(individual_section):
            if axis_id in sections:
                raise ConfigurationError(
                    f"Axis {axis_id} has individual and grouped parameters"
                )
            sections[axis_id] = individual_section
        if axis_id not in sections:
            raise ConfigurationError(f"No parameters configured for axis {axis_id}")
    return sections


def load_configuration(path: Path | str = CONFIG_PATH) -> ApplicationConfig:
    """Load and validate a complete application configuration."""

    ini_path = Path(path)
    parser = ConfigParser(interpolation=None)
    if not parser.read(ini_path, encoding="utf-8"):
        raise ConfigurationError(f"Configuration file not found: {ini_path}")

    try:
        connection_count = parser.getint("modbus", "connection_count")
        connections = tuple(
            ModbusConnectionConfig(
                parser.get(f"modbus_connection_{index}", "port").strip(),
                frozenset(
                    _csv_ints(parser.get(f"modbus_connection_{index}", "axes"))
                ),
            )
            for index in range(1, connection_count + 1)
        )
        modbus_config = ModbusConfig(
            connections=connections,
            baud=parser.getint("modbus", "baud"),
            serial_timeout_s=parser.getfloat("modbus", "serial_timeout_s"),
            inter_frame_delay_s=parser.getfloat("modbus", "inter_frame_delay_s"),
            response_poll_delay_s=parser.getfloat(
                "modbus", "response_poll_delay_s"
            ),
            queue_drain_timeout_s=parser.getfloat(
                "modbus", "queue_drain_timeout_s"
            ),
            task_wait_timeout_s=parser.getfloat("modbus", "task_wait_timeout_s"),
            worker_join_timeout_s=parser.getfloat(
                "modbus", "worker_join_timeout_s"
            ),
        )

        maximum_axes = parser.getint("rig", "maximum_axes")
        axis_sections = _axis_parameter_sections(parser, maximum_axes)
        axes = tuple(
            AxisConfig(
                axis_id=index,
                enabled=parser.getboolean(axis_sections[index], "enabled"),
                spindle_pitch_mm=parser.getfloat(
                    axis_sections[index], "spindle_pitch_mm"
                ),
                stroke_mm=parser.getfloat(axis_sections[index], "stroke_mm"),
                zero_offset_mm=parser.getfloat(
                    axis_sections[index], "zero_offset_mm"
                ),
                speed_mm_s=parser.getfloat(
                    axis_sections[index], "speed_mm_s"
                ),
                acc_time_ms=parser.getint(axis_sections[index], "accTime"),
                dec_time_ms=parser.getint(axis_sections[index], "decTime"),
            )
            for index in range(1, maximum_axes + 1)
        )
        limits = MotionLimits(
            minimum_mm=parser.getfloat("motion_limits", "minimum_mm"),
            maximum_mm=parser.getfloat("motion_limits", "maximum_mm"),
            homing_mm=parser.getfloat("motion_limits", "homing_mm"),
            reference_spindle_pitch_mm=parser.getfloat(
                "motion_limits", "reference_spindle_pitch_mm"
            ),
            reference_app_units_per_mm=parser.getint(
                "motion_limits", "reference_app_units_per_mm"
            ),
            raw_position_max=parser.getint("motion_limits", "raw_position_max"),
        )
        rig_config = RigConfig(
            axes=axes,
            limits=limits,
            distance_front_drives_left_to_right_mm=parser.getfloat(
                "rig", "DistanceFrontDrivesLeft2Right"
            ),
            distance_rear_drives_left_to_right_mm=parser.getfloat(
                "rig", "DistanceRearDrivesLeft2Right"
            ),
            distance_front_to_rear_drives_mm=parser.getfloat(
                "rig", "DistanceFront2RearDrives"
            ),
            center_of_gravity_front_to_rear_mm=parser.getfloat(
                "rig", "CenterOfGravityFront2Rear"
            ),
            center_of_gravity_left_to_right_mm=parser.getfloat(
                "rig", "CenterOfGravityLeft2Right"
            ),
            center_of_gravity_height_mm=parser.getfloat(
                "rig", "CenterOfGravityHeight"
            ),
        )

        a6_config = A6Config(
            poll_interval_s=parser.getfloat("a6", "poll_interval_s"),
            homing_start_observation_s=parser.getfloat(
                "a6", "homing_start_observation_s"
            ),
            operation_timeout_s=parser.getfloat("a6", "operation_timeout_s"),
            invert_digital_inputs=parser.getboolean(
                "a6", "invert_digital_inputs"
            ),
            homing_initial_rpm=parser.getint("a6", "homing_initial_rpm"),
            homing_end_rpm=parser.getint("a6", "homing_end_rpm"),
            homing_accel_ms=parser.getint("a6", "homing_accel_ms"),
            homing_decel_ms=parser.getint("a6", "homing_decel_ms"),
            planner_start_rpm=parser.getint("a6", "planner_start_rpm"),
            planner_start_accel_ms=parser.getint(
                "a6", "planner_start_accel_ms"
            ),
            planner_start_decel_ms=parser.getint(
                "a6", "planner_start_decel_ms"
            ),
            move_rpm=parser.getint("a6", "move_rpm"),
            move_accel_ms=parser.getint("a6", "move_accel_ms"),
            move_decel_ms=parser.getint("a6", "move_decel_ms"),
        )
        simhub_config = SimHubConfig(
            udp_ip=parser.get("simhub", "udp_ip"),
            udp_port=parser.getint("simhub", "udp_port"),
            disconnect_timeout_s=parser.getfloat(
                "simhub", "disconnect_timeout_s"
            ),
            sender_cycle_s=parser.getfloat("simhub", "sender_cycle_s"),
            position_count=parser.getint("simhub", "position_count"),
            position_min=parser.getint("simhub", "position_min"),
            position_max=parser.getint("simhub", "position_max"),
            data_retention_days=parser.getint("simhub", "data_retention_days"),
        )
        control_config = ControlConfig(
            hub_axis_from=parser.getint("control", "hub_axis_from"),
            hub_axis_to=parser.getint("control", "hub_axis_to"),
            hub_status_poll_interval_s=parser.getfloat(
                "control", "hub_status_poll_interval_s"
            ),
            actual_position_poll_interval_s=parser.getfloat(
                "control", "actual_position_poll_interval_s"
            ),
            dynamic_accel_step_ms=parser.getint(
                "control", "dynamic_accel_step_ms"
            ),
            dynamic_parameter_update_interval_s=parser.getfloat(
                "control", "dynamic_parameter_update_interval_s"
            ),
            center_rpm=parser.getint("control", "center_rpm"),
            center_accel_ms=parser.getint("control", "center_accel_ms"),
            center_decel_ms=parser.getint("control", "center_decel_ms"),
            maintenance_middle_rpm=parser.getint(
                "control", "maintenance_middle_rpm"
            ),
            maintenance_hub_rpm=parser.getint(
                "control", "maintenance_hub_rpm"
            ),
            axis_wait_timeout_s=parser.getfloat(
                "control", "axis_wait_timeout_s"
            ),
            homing_wait_timeout_s=parser.getfloat(
                "control", "homing_wait_timeout_s"
            ),
            wait_poll_interval_s=parser.getfloat(
                "control", "wait_poll_interval_s"
            ),
        )
        leveling_stages = tuple(
            LevelingStageConfig(
                name=name,
                step_mm=parser.getfloat(f"leveling_stage_{name}", "step_mm"),
                max_iterations=parser.getint(
                    f"leveling_stage_{name}", "max_iterations"
                ),
                target_tolerance=parser.getfloat(
                    f"leveling_stage_{name}", "target_tolerance"
                ),
                kp=parser.getfloat(f"leveling_stage_{name}", "kp"),
                allow_lowering=parser.getboolean(
                    f"leveling_stage_{name}", "allow_lowering"
                ),
                filter_alpha=parser.getfloat(
                    f"leveling_stage_{name}", "filter_alpha"
                ),
                lower_step_factor=parser.getfloat(
                    f"leveling_stage_{name}", "lower_step_factor"
                ),
                raw_weight=parser.getfloat(
                    f"leveling_stage_{name}", "raw_weight"
                ),
                integral_gain=parser.getfloat(
                    f"leveling_stage_{name}", "integral_gain"
                ),
            )
            for name in ("coarse", "medium", "fine")
        )
        leveling_config = LevelingConfig(
            load_rate_units_per_percent=parser.getfloat(
                "leveling", "load_rate_units_per_percent"
            ),
            load_bias_by_axis=_axis_float_map(
                parser.get("leveling", "load_bias_by_axis")
            ),
            load_rate_samples=parser.getint("leveling", "load_rate_samples"),
            load_rate_sample_delay_s=parser.getfloat(
                "leveling", "load_rate_sample_delay_s"
            ),
            settle_time_s=parser.getfloat("leveling", "settle_time_s"),
            settle_checks=parser.getint("leveling", "settle_checks"),
            settle_check_delay_s=parser.getfloat(
                "leveling", "settle_check_delay_s"
            ),
            settle_load_delta=parser.getfloat(
                "leveling", "settle_load_delta"
            ),
            verify_batches=parser.getint("leveling", "verify_batches"),
            verify_corrections=parser.getint(
                "leveling", "verify_corrections"
            ),
            minimum_tolerance=parser.getfloat(
                "leveling", "minimum_tolerance"
            ),
            offset_axes=_csv_ints(parser.get("leveling", "offset_axes")),
            stages=leveling_stages,
        )
        grease_config = GreaseConfig(
            rpm=parser.getint("grease", "rpm"),
            accel_ms=parser.getint("grease", "accel_ms"),
            decel_ms=parser.getint("grease", "decel_ms"),
            negative_mm=parser.getfloat("grease", "negative_mm"),
            positive_mm=parser.getfloat("grease", "positive_mm"),
            center_mm=parser.getfloat("grease", "center_mm"),
            move_timeout_s=parser.getfloat("grease", "move_timeout_s"),
            position_poll_interval_s=parser.getfloat(
                "grease", "position_poll_interval_s"
            ),
            warning_after_operating_hours=parser.getfloat(
                "grease", "warning_after_operating_hours"
            ),
            alarm_after_operating_hours=parser.getfloat(
                "grease", "alarm_after_operating_hours"
            ),
        )
        ui_config = UiConfig(
            refresh_ms=parser.getint("ui", "refresh_ms"),
            axis_status_refresh_ms=parser.getint(
                "ui", "axis_status_refresh_ms"
            ),
            trigger_chart_refresh_ms=parser.getint(
                "ui", "trigger_chart_refresh_ms"
            ),
            trigger_chart_window_s=parser.getfloat(
                "ui", "trigger_chart_window_s"
            ),
            trigger_chart_max_ms=parser.getfloat("ui", "trigger_chart_max_ms"),
            disconnected_background=parser.get(
                "ui", "disconnected_background"
            ),
            connected_background=parser.get("ui", "connected_background"),
        )
        room_light_config = RoomLightConfig(
            uri=parser.get("room_light", "uri"),
            request_timeout_s=parser.getfloat(
                "room_light", "request_timeout_s"
            ),
        )
    except Exception as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError(f"Invalid configuration in {ini_path}: {exc}") from exc

    enabled_axes = {axis.axis_id for axis in axes if axis.enabled}
    routed_axes = [axis for connection in connections for axis in connection.axes]
    if not connections or any(
        not connection.port or not connection.axes for connection in connections
    ):
        raise ConfigurationError("Every Modbus connection needs a port and axes")
    if len(set(routed_axes)) != len(routed_axes):
        raise ConfigurationError("Each Modbus axis must occur on exactly one connection")
    configured_axes = {axis.axis_id for axis in axes}
    if not set(routed_axes).issubset(configured_axes):
        raise ConfigurationError("Modbus connections contain an unknown axis")
    if not enabled_axes.issubset(routed_axes):
        raise ConfigurationError(
            "Every enabled axis must be assigned to a Modbus connection"
        )
    active_connections = tuple(
        replace(connection, axes=connection.axes & enabled_axes)
        for connection in connections
        if connection.axes & enabled_axes
    )
    modbus_config = replace(modbus_config, connections=active_connections)
    if limits.minimum_mm >= limits.maximum_mm:
        raise ConfigurationError("minimum_mm must be smaller than maximum_mm")
    if not 1 <= simhub_config.udp_port <= 65535:
        raise ConfigurationError("udp_port must be between 1 and 65535")
    if simhub_config.position_count != len(axes):
        raise ConfigurationError("position_count must equal the configured axis count")
    if simhub_config.position_min >= simhub_config.position_max:
        raise ConfigurationError("position_min must be smaller than position_max")
    if not 1 <= control_config.hub_axis_from <= control_config.hub_axis_to <= len(axes):
        raise ConfigurationError("The configured hub axis range is invalid")
    if not leveling_config.offset_axes:
        raise ConfigurationError("At least one leveling offset axis is required")
    if not set(leveling_config.offset_axes).issubset(configured_axes):
        raise ConfigurationError("Leveling offset axes must be configured")
    for stage in leveling_config.stages:
        if stage.max_iterations <= 0 or stage.step_mm <= 0:
            raise ConfigurationError(
                f"Leveling stage {stage.name} needs positive iterations and step_mm"
            )
        if not 0 <= stage.filter_alpha <= 1 or not 0 <= stage.raw_weight <= 1:
            raise ConfigurationError(
                f"Leveling stage {stage.name} weights must be between zero and one"
            )
    if not (
        0
        < grease_config.warning_after_operating_hours
        < grease_config.alarm_after_operating_hours
    ):
        raise ConfigurationError(
            "Grease warning hours must be positive and lower than alarm hours"
        )

    positive_values = {
        "baud": modbus_config.baud,
        "serial_timeout_s": modbus_config.serial_timeout_s,
        "inter_frame_delay_s": modbus_config.inter_frame_delay_s,
        "operation_timeout_s": a6_config.operation_timeout_s,
        "sender_cycle_s": simhub_config.sender_cycle_s,
        "actual_position_poll_interval_s": (
            control_config.actual_position_poll_interval_s
        ),
        "maintenance_middle_rpm": control_config.maintenance_middle_rpm,
        "maintenance_hub_rpm": control_config.maintenance_hub_rpm,
        "data_retention_days": simhub_config.data_retention_days,
        "reference_spindle_pitch_mm": limits.reference_spindle_pitch_mm,
        "reference_app_units_per_mm": limits.reference_app_units_per_mm,
        "DistanceFrontDrivesLeft2Right": (
            rig_config.distance_front_drives_left_to_right_mm
        ),
        "DistanceRearDrivesLeft2Right": (
            rig_config.distance_rear_drives_left_to_right_mm
        ),
        "DistanceFront2RearDrives": rig_config.distance_front_to_rear_drives_mm,
        "CenterOfGravityHeight": rig_config.center_of_gravity_height_mm,
        "ui.refresh_ms": ui_config.refresh_ms,
        "ui.axis_status_refresh_ms": ui_config.axis_status_refresh_ms,
        "room_light.request_timeout_s": room_light_config.request_timeout_s,
    }
    for name, value in positive_values.items():
        _positive(name, value)
    for axis in axes:
        _positive(f"axis_{axis.axis_id}.spindle_pitch_mm", axis.spindle_pitch_mm)
        _positive(f"axis_{axis.axis_id}.speed_mm_s", axis.speed_mm_s)
        if axis.acc_time_ms < 100 or axis.dec_time_ms < 100:
            raise ConfigurationError(
                f"axis_{axis.axis_id} accTime and decTime must be at least 100 ms"
            )

    return ApplicationConfig(
        modbus=modbus_config,
        rig=rig_config,
        a6=a6_config,
        simhub=simhub_config,
        control=control_config,
        leveling=leveling_config,
        grease=grease_config,
        ui=ui_config,
        room_light=room_light_config,
    )


APP_CONFIG = load_configuration()
MODBUS_CONFIG = APP_CONFIG.modbus
RIG_CONFIG = APP_CONFIG.rig
A6_CONFIG = APP_CONFIG.a6
SIMHUB_CONFIG = APP_CONFIG.simhub
CONTROL_CONFIG = APP_CONFIG.control
LEVELING_CONFIG = APP_CONFIG.leveling
GREASE_CONFIG = APP_CONFIG.grease
UI_CONFIG = APP_CONFIG.ui
ROOM_LIGHT_CONFIG = APP_CONFIG.room_light
