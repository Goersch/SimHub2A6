#!/usr/bin/env python3

import ctypes
import sys
import threading
import time
import tkinter as tk
from collections import deque
from contextlib import nullcontext
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from statistics import median
from tkinter import font, messagebox

SCRIPT_DIR = Path(__file__).resolve().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(SCRIPT_DIR.parent))
    __package__ = SCRIPT_DIR.name

simhub = None

from . import Analyse, Grease, Leveling, Wartung
from .LIB.a6_motion_controller import motion_controller
from .LIB.a6_simu import a6_simulator
from .LIB.config import CONTROL_CONFIG, GREASE_CONFIG, RIG_CONFIG, SIMHUB_CONFIG, UI_CONFIG
from .LIB.language import text as language_text
from .LIB.logging_config import configure_logging, get_logger
from .LIB.room_light import set_room_light
from .LIB.simrig_load_calibration import save_load_calibration
from .LIB.ui_panels import ChartPanel, LevelingPanel, LightPanel, MotorPanel
from .LIB.ui_widgets import BarGraph, TriggerIntervalChart

logger = get_logger("ui.dialog")


VALUE_MIN = SIMHUB_CONFIG.position_min
VALUE_MAX = SIMHUB_CONFIG.position_max
REFRESH_MS = UI_CONFIG.refresh_ms
AXIS_STATUS_REFRESH_MS = UI_CONFIG.axis_status_refresh_ms
TRIGGER_CHART_REFRESH_MS = UI_CONFIG.trigger_chart_refresh_ms
TRIGGER_CHART_WINDOW_S = UI_CONFIG.trigger_chart_window_s
TRIGGER_CHART_MAX_MS = UI_CONFIG.trigger_chart_max_ms
MOTOR_COUNT = SIMHUB_CONFIG.position_count
ACTUAL_POSITION_POLL_INTERVAL_S = CONTROL_CONFIG.actual_position_poll_interval_s
SIMHUB_DISCONNECTED_BG = UI_CONFIG.disconnected_background
SIMHUB_CONNECTED_BG = UI_CONFIG.connected_background
TABLE_HEADER_BG = "#4A6D92"
TABLE_HEADER_FG = "#FFFFFF"
TABLE_CELL_BG = "#FFFFFF"
LEVELING_FIXED_AXIS_BG = "#DDF2DD"
TABLE_CELL_FG = "#111111"
TABLE_WARNING_BG = "#FFF3A3"
TABLE_ERROR_BG = "#F8B4B4"
TABLE_BORDER = "#B7C6D8"
AXIS_LABELS = {
    1: language_text("Common", "axis_front"),
    2: language_text("Common", "axis_middle"),
    3: language_text("Common", "axis_rear"),
    4: language_text("Common", "axis_front_left"),
    5: language_text("Common", "axis_front_right"),
    6: language_text("Common", "axis_rear_right"),
    7: language_text("Common", "axis_rear_left"),
    (4, 7): language_text("Common", "axis_hub"),
}

ERROR_STATE_TEXT = {
    0: language_text("Common", "status_ok"),
    1: language_text("MainDialog", "error_servo_off"),
    2: language_text("MainDialog", "error_ready"),
    3: language_text("MainDialog", "error_start_locked"),
    4: language_text("MainDialog", "error_positioning_locked"),
    5: language_text("Common", "status_ok"),
}



def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def grease_status(playtime_minutes):
    try:
        operating_hours = max(0.0, float(playtime_minutes)) / 60.0
    except (TypeError, ValueError):
        operating_hours = 0.0
    if operating_hours > GREASE_CONFIG.alarm_after_operating_hours:
        return language_text("MainDialog", "grease_required"), TABLE_ERROR_BG
    if operating_hours > GREASE_CONFIG.warning_after_operating_hours:
        return language_text("MainDialog", "grease_soon_required"), TABLE_WARNING_BG
    return language_text("Common", "status_ok"), TABLE_CELL_BG


def get_second_monitor_rect():
    if sys.platform != "win32":
        return None

    try:
        user32 = ctypes.windll.user32
        monitors = []

        MonitorEnumProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT),
            ctypes.c_long,
        )

        def _monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
            rect = lprcMonitor.contents
            monitors.append((rect.left, rect.top, rect.right, rect.bottom))
            return True

        callback = MonitorEnumProc(_monitor_enum_proc)
        if not user32.EnumDisplayMonitors(0, 0, callback, 0):
            return None

        return monitors[1] if len(monitors) > 1 else None
    except Exception:
        return None


def get_simhub_module():
    global simhub
    if simhub is not None:
        return simhub

    simhub = sys.modules.get("SimHub2SimRig")
    if simhub is None or getattr(simhub, "latestValues", None) is None:
        simhub = sys.modules.get("__main__")

    if simhub is not None and getattr(simhub, "latestValues", None) is not None:
        return simhub

    try:
        from . import SimHub2SimRig as imported_simhub
    except Exception:
        imported_simhub = None

    simhub = imported_simhub
    return simhub


def get_simhub_commands_module():
    current_simhub = get_simhub_module()
    shcmd = getattr(current_simhub, 'shcmd', None) if current_simhub is not None else None
    if shcmd is None:
        shcmd = sys.modules.get('SimHub2A6.SimHubCommands') or sys.modules.get('SimHubCommands')
    return shcmd
def get_leveling_offset(axis):
    offsets = Leveling.levelingOffset
    if offsets is None or not 1 <= axis <= len(offsets):
        return None

    return offsets[axis - 1]


def room_light(status):
    set_room_light(bool(status))


roomLight = room_light


class Dialog(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(language_text("MainDialog", "title"))
        self.configure(bg=SIMHUB_DISCONNECTED_BG)
        self._closing = False
        self._refresh_after_id = None
        self._motor_after_id = None
        self._trigger_chart_after_id = None
        self._trigger_chart_samples = deque()
        self._apply_motor_after_ids = set()
        self._motor_refresh_in_progress = False
        self._motor_next_axis = 1
        self._leveling_locked = False
        self._leveling_running = False
        self._leveling_stop_requested = False
        self._load_capture_running = False
        self._analyse_dialog = None
        self._maintenance_dialog = None
        self.motion_mode = tk.StringVar(value="simhub")
        self._motion_mode_busy = False
        self._actual_position_reader_enabled = threading.Event()
        self._actual_position_reader_stop = threading.Event()
        self._actual_position_read_errors = set()
        a6_simulator.set_read_actual_positions_enabled(False)

        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(2, weight=0)
        self.rowconfigure(3, weight=1)
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)

        self._build_layout()
        self._move_to_second_monitor()

        self.bar0 = BarGraph(self.top_frame, orientation="horizontal", width=210, height=35, bg=SIMHUB_DISCONNECTED_BG)
        self.bar1 = BarGraph(self.mid_frame, orientation="vertical", width=30, height=210, bg=SIMHUB_DISCONNECTED_BG)
        self.bar2 = BarGraph(self.bottom_frame, orientation="horizontal", width=210, height=35, bg=SIMHUB_DISCONNECTED_BG)
        self.bar3 = BarGraph(self.top_frame, orientation="vertical", width=42, height=180, bg=SIMHUB_DISCONNECTED_BG)
        self.bar4 = BarGraph(self.top_frame, orientation="vertical", width=42, height=180, bg=SIMHUB_DISCONNECTED_BG)
        self.bar5 = BarGraph(self.bottom_frame, orientation="vertical", width=42, height=180, bg=SIMHUB_DISCONNECTED_BG)
        self.bar6 = BarGraph(self.bottom_frame, orientation="vertical", width=42, height=180, bg=SIMHUB_DISCONNECTED_BG)

        self.bar3.grid(row=0, column=0, padx=(0, 10), pady=0)
        self.bar0.grid(row=0, column=1, padx=0, pady=28)
        self.bar4.grid(row=0, column=2, padx=(10, 0), pady=0)

        self.bar1.grid(row=1, column=1, pady=0)

        self.bar6.grid(row=0, column=0, padx=(0, 10), pady=0)
        self.bar2.grid(row=0, column=1, padx=0, pady=28)
        self.bar5.grid(row=0, column=2, padx=(10, 0), pady=0)

        self._update_connection_status()
        self._refresh_after_id = self.after(REFRESH_MS, self._refresh_values)
        self._motor_after_id = self.after(
            max(1, AXIS_STATUS_REFRESH_MS // MOTOR_COUNT),
            self._refresh_motor_table,
        )
        self._trigger_chart_after_id = self.after(
            TRIGGER_CHART_REFRESH_MS,
            self._refresh_trigger_chart,
        )
        self._actual_position_reader_thread = threading.Thread(
            target=self._actual_position_reader_worker,
            name="A6ActualPositionReader",
            daemon=True,
        )
        self._actual_position_reader_thread.start()

    def close(self):
        if self._closing:
            return

        self._closing = True
        try:
            self._actual_position_reader_stop.set()
            self._actual_position_reader_enabled.set()
            a6_simulator.set_read_actual_positions_enabled(False)
            if self._analyse_dialog is not None:
                self._analyse_dialog.close()
            if self._maintenance_dialog is not None:
                self._maintenance_dialog.close()
            if self._leveling_running:
                Leveling.stop_leveling()
            Grease.update_playtime(self._simhub_playing())
            Grease.save_grease_data()
            for after_id in (
                self._refresh_after_id,
                self._motor_after_id,
                self._trigger_chart_after_id,
            ):
                if after_id is None:
                    continue
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
            self._refresh_after_id = None
            self._motor_after_id = None
            self._trigger_chart_after_id = None

            for after_id in list(self._apply_motor_after_ids):
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                self._apply_motor_after_ids.discard(after_id)
            actual_position_thread = self._actual_position_reader_thread
            if actual_position_thread is not threading.current_thread():
                actual_position_thread.join(timeout=1.0)
        finally:
            try:
                if self.winfo_exists():
                    self.destroy()
            except tk.TclError:
                pass

    def _move_to_second_monitor(self):
        rect = get_second_monitor_rect()
        if rect is None:
            return

        left, top, right, bottom = rect
        self.update_idletasks()
        self.geometry(f"+{left}+{top}")

    def _simhub_connected(self):
        current_simhub = get_simhub_module()
        if current_simhub is None:
            return False
        return getattr(current_simhub, "SimHubConnected", False)

    def _simhub_playing(self):
        current_simhub = get_simhub_module()
        if current_simhub is None:
            return False
        return getattr(current_simhub, "SimHubGameRunning", False)

    def _update_connection_status(self):
        if self._closing or not self.winfo_exists():
            return

        connected = self._simhub_connected()

        if connected:
            self._leveling_locked = True
        self.leveling_start_button.configure(
            state=(
                tk.DISABLED
                if (
                    self._leveling_locked
                    or self._leveling_running
                    or self._load_capture_running
                )
                else tk.NORMAL
            )
        )
        self.leveling_stop_button.configure(
            state=(
                tk.NORMAL
                if self._leveling_running and not self._leveling_stop_requested
                else tk.DISABLED
            )
        )
        load_capture_blocked = (
            self._load_capture_running
            or self._leveling_running
            or Grease.greaseActive
            or self._motion_mode_busy
            or self._simhub_playing()
        )
        self.save_load_values_button.configure(
            state=tk.DISABLED if load_capture_blocked else tk.NORMAL
        )
        self._update_analyse_button()
        self._update_maintenance_button(connected)
        maintenance_dialog = self._maintenance_dialog
        if maintenance_dialog is not None:
            try:
                if maintenance_dialog.winfo_exists():
                    maintenance_dialog.set_simhub_connected(connected)
            except tk.TclError:
                pass

        bg = SIMHUB_CONNECTED_BG if connected else SIMHUB_DISCONNECTED_BG

        self.configure(bg=bg)
        self.header_frame.configure(bg=bg)
        self.left_frame.configure(bg=bg)
        self.top_frame.configure(bg=bg)
        self.mid_frame.configure(bg=bg)
        self.bottom_frame.configure(bg=bg)
        self.motor_table_frame.configure(bg=bg)
        self.light_button_frame.configure(bg=bg)
        self.leveling_panel.configure(bg=bg)
        self.light_panel.configure(bg=bg)
        self.chart_panel.configure(bg=bg)
        self.time_label.configure(bg=bg, fg="#AAAAAA")

        for bar in [self.bar0, self.bar1, self.bar2, self.bar3, self.bar4, self.bar5, self.bar6]:
            bar.set_bg(bg)

    def _build_layout(self):
        self.header_frame = tk.Frame(self, bg=SIMHUB_DISCONNECTED_BG)
        self.header_frame.grid(row=0, column=0, padx=14, pady=(14, 4), sticky="nsew", rowspan=1)

        self.time_label = tk.Label(
            self.header_frame,
            text="",
            font=font.Font(family="Segoe UI", size=14, weight="bold"),
            bg=SIMHUB_DISCONNECTED_BG,
            fg="#AAAAAA"
        )
        self.time_label.pack(expand=True)

        self.left_frame = tk.Frame(self, bg=SIMHUB_DISCONNECTED_BG)
        self.left_frame.grid(row=1, column=0, rowspan=3, padx=14, pady=(4, 14), sticky="nw")

        self.top_frame = tk.Frame(self.left_frame, bg=SIMHUB_DISCONNECTED_BG)
        self.top_frame.grid(row=0, column=0, padx=0, pady=(0, 4), sticky="nw")

        self.mid_frame = tk.Frame(self.left_frame, bg=SIMHUB_DISCONNECTED_BG)
        self.mid_frame.grid(row=1, column=0, padx=0, pady=4, sticky="nsew")

        self.bottom_frame = tk.Frame(self.left_frame, bg=SIMHUB_DISCONNECTED_BG)
        self.bottom_frame.grid(row=2, column=0, padx=0, pady=(4, 0), sticky="nw")

        self.motor_table_frame = MotorPanel(self, bg=SIMHUB_DISCONNECTED_BG)
        self.motor_table_frame.grid(
            row=0,
            column=1,
            rowspan=4,
            padx=(6, 0),
            pady=(14, 0),
            sticky="nsew",
        )
        colors = (TABLE_HEADER_BG, TABLE_HEADER_FG, TABLE_CELL_BG, TABLE_CELL_FG)
        self.motor_value_labels = self.motor_table_frame.build_table(
            AXIS_LABELS, MOTOR_COUNT, colors
        )

        self.light_button_frame = tk.Frame(self.motor_table_frame, bg=SIMHUB_DISCONNECTED_BG)
        self.light_button_frame.grid(
            row=MOTOR_COUNT + 2,
            column=0,
            columnspan=5,
            pady=(12, 0),
            sticky="e",
        )
        self._build_light_buttons()

        self.chart_panel = ChartPanel(self.motor_table_frame, bg=SIMHUB_DISCONNECTED_BG)
        self.chart_panel.grid(
            row=MOTOR_COUNT + 3,
            column=0,
            columnspan=5,
            sticky="nsew",
        )
        self.motor_table_frame.grid_rowconfigure(MOTOR_COUNT + 3, weight=1)
        self.chart_panel.grid_rowconfigure(0, weight=1)
        self.chart_panel.grid_columnconfigure(0, weight=1)
        self.trigger_interval_chart = TriggerIntervalChart(self.chart_panel)
        self.trigger_interval_chart.grid(
            row=0,
            column=0,
            pady=(14, 0),
            sticky="nsew",
        )

        self.top_frame.grid_columnconfigure(0, weight=0)
        self.top_frame.grid_columnconfigure(1, weight=0)
        self.top_frame.grid_columnconfigure(2, weight=0)
        self.top_frame.grid_rowconfigure(0, weight=0)

        self.mid_frame.grid_columnconfigure(0, weight=1)
        self.mid_frame.grid_columnconfigure(1, weight=0)
        self.mid_frame.grid_columnconfigure(2, weight=1)
        self.mid_frame.grid_rowconfigure(0, weight=1)
        self.mid_frame.grid_rowconfigure(1, weight=0)
        self.mid_frame.grid_rowconfigure(2, weight=1)

        self.bottom_frame.grid_columnconfigure(0, weight=0)
        self.bottom_frame.grid_columnconfigure(1, weight=0)
        self.bottom_frame.grid_columnconfigure(2, weight=0)
        self.bottom_frame.grid_rowconfigure(0, weight=0)

    def _build_light_buttons(self):
        self.leveling_panel = LevelingPanel(
            self.light_button_frame, bg=SIMHUB_DISCONNECTED_BG
        )
        self.light_panel = LightPanel(
            self.light_button_frame, bg=SIMHUB_DISCONNECTED_BG
        )
        self.leveling_panel.grid(row=0, column=0, sticky="ns")
        self.light_panel.grid(row=0, column=1, padx=(12, 0), sticky="ns")
        (
            self.leveling_start_button,
            self.leveling_stop_button,
            self.save_load_values_button,
        ) = self.leveling_panel.build_controls(
            self._run_leveling,
            self._stop_leveling,
            self._save_load_values,
        )
        (
            self.analyse_button,
            self.maintenance_button,
            self.simhub_mode_button,
            self.actual_positions_mode_button,
            self.center_mode_button,
        ) = self.light_panel.build_controls(
            room_light,
            self._open_analyse_dialog,
            self._open_maintenance_dialog,
            self.motion_mode,
            self._set_motion_mode,
        )

    def _set_actual_position_reader_enabled(self, enabled):
        a6_simulator.set_read_actual_positions_enabled(enabled)
        if enabled:
            self._actual_position_reader_enabled.set()
        else:
            self._actual_position_reader_enabled.clear()

    def _actual_position_reader_worker(self):
        axes = tuple(axis.axis_id for axis in RIG_CONFIG.axes if axis.enabled)
        axis_index = 0
        while not self._actual_position_reader_stop.is_set():
            if not self._actual_position_reader_enabled.wait(timeout=0.2):
                continue
            if self._actual_position_reader_stop.is_set():
                break

            axis = axes[axis_index]
            axis_index = (axis_index + 1) % len(axes)
            try:
                position_mm = (
                    motion_controller.read_position_mm(axis)
                    - RIG_CONFIG.axis(axis).zero_offset_mm
                )
                a6_simulator.set_read_actual_position(axis, position_mm)
                self._actual_position_read_errors.discard(axis)
            except Exception as error:
                if axis not in self._actual_position_read_errors:
                    self._actual_position_read_errors.add(axis)
                    logger.warning(
                        "Actual position for axis %s could not be read: %s",
                        axis,
                        error,
                    )
            self._actual_position_reader_stop.wait(
                ACTUAL_POSITION_POLL_INTERVAL_S
            )
    def _set_motion_mode(self):
        if self._motion_mode_busy or self._load_capture_running:
            return

        mode = self.motion_mode.get()
        self._set_actual_position_reader_enabled(mode == "actual_positions")

        shcmd = get_simhub_commands_module()
        if shcmd is None:
            self.motion_mode.set("simhub")
            self._set_actual_position_reader_enabled(False)
            return

        center_mode = mode == "center"
        set_enabled = getattr(shcmd, "set_simhub_positions_enabled", None)
        if set_enabled is None:
            self.motion_mode.set("simhub")
            self._set_actual_position_reader_enabled(False)
            return

        set_enabled(not center_mode)
        if not center_mode:
            return

        self._motion_mode_busy = True
        self.simhub_mode_button.configure(state=tk.DISABLED)
        self.actual_positions_mode_button.configure(state=tk.DISABLED)
        self.center_mode_button.configure(state=tk.DISABLED)
        threading.Thread(
            target=self._center_all_axes_worker,
            args=(shcmd,),
            daemon=True,
        ).start()

    def _center_all_axes_worker(self, shcmd):
        error = None
        try:
            shcmd.center_all_axes()
        except Exception as exc:
            error = exc

        if self._closing:
            return
        try:
            self.after(0, self._finish_center_all_axes, error)
        except tk.TclError:
            pass

    def _finish_center_all_axes(self, error):
        self._motion_mode_busy = False
        if self._closing or not self.winfo_exists():
            return

        self.simhub_mode_button.configure(state=tk.NORMAL)
        self.actual_positions_mode_button.configure(state=tk.NORMAL)
        self.center_mode_button.configure(state=tk.NORMAL)
        if error is not None:
            logger.error("Centering axes failed: %s", error)
            messagebox.showerror(
                language_text("MainDialog", "center_title"),
                language_text("MainDialog", "center_error", error=error),
                parent=self,
            )

    def _analyse_dialog_is_open(self):
        if self._analyse_dialog is None:
            return False
        try:
            return bool(self._analyse_dialog.winfo_exists())
        except tk.TclError:
            return False

    def _maintenance_dialog_is_open(self):
        if self._maintenance_dialog is None:
            return False
        try:
            return bool(self._maintenance_dialog.winfo_exists())
        except tk.TclError:
            return False

    def _update_maintenance_button(self, connected=None):
        if connected is None:
            connected = self._simhub_connected()
        disabled = connected or self._maintenance_dialog_is_open()
        self.maintenance_button.configure(
            state=tk.DISABLED if disabled else tk.NORMAL
        )

    def _open_maintenance_dialog(self):
        if self._simhub_connected() or self._maintenance_dialog_is_open():
            return

        shcmd = get_simhub_commands_module()
        if shcmd is None:
            messagebox.showerror(
                language_text("Panels", "maintenance"),
                language_text("MainDialog", "maintenance_unavailable"),
                parent=self,
            )
            return

        self._maintenance_dialog = Wartung.WartungDialog(
            self,
            shcmd,
            self._simhub_connected,
            self._maintenance_dialog_closed,
        )
        self._update_maintenance_button()

    def _maintenance_dialog_closed(self):
        self._maintenance_dialog = None
        if not self._closing and self.winfo_exists():
            self._update_maintenance_button()

    def _update_analyse_button(self):
        disabled = self._simhub_playing() or self._analyse_dialog_is_open()
        self.analyse_button.configure(state=tk.DISABLED if disabled else tk.NORMAL)

    def _open_analyse_dialog(self):
        if self._simhub_playing() or self._analyse_dialog_is_open():
            return

        try:
            self._analyse_dialog = Analyse.AnalyseDialog(
                self,
                on_close=self._analyse_dialog_closed,
            )
        except Exception as error:
            Analyse.analyseActive = False
            self._analyse_dialog = None
            logger.exception("Analysis dialog could not be opened")
            messagebox.showerror(
                language_text("Panels", "analysis"),
                language_text("MainDialog", "analysis_open_error", error=error),
                parent=self,
            )
            return
        self._update_analyse_button()

    def _analyse_dialog_closed(self):
        self._analyse_dialog = None
        if not self._closing and self.winfo_exists():
            self._update_analyse_button()

    def _run_leveling(self):
        if (
            self._leveling_locked
            or self._leveling_running
            or self._load_capture_running
        ):
            return

        self._leveling_running = True
        self._leveling_stop_requested = False
        self.leveling_start_button.configure(state=tk.DISABLED)
        self.leveling_stop_button.configure(state=tk.NORMAL)
        threading.Thread(target=self._leveling_worker, daemon=True).start()

    def _stop_leveling(self):
        if not self._leveling_running or self._leveling_stop_requested:
            return

        self._leveling_stop_requested = True
        self.leveling_stop_button.configure(state=tk.DISABLED)
        Leveling.stop_leveling()

    def _leveling_worker(self):
        try:
            Leveling.leveling()
        except Exception:
            logger.exception("Leveling failed")
        finally:
            if not self._closing:
                try:
                    self.after(0, self._finish_leveling)
                except tk.TclError:
                    pass

    def _finish_leveling(self):
        self._leveling_running = False
        self._leveling_stop_requested = False
        if self._closing or not self.winfo_exists():
            return
        self.leveling_start_button.configure(
            state=tk.DISABLED if self._leveling_locked else tk.NORMAL
        )
        self.leveling_stop_button.configure(state=tk.DISABLED)

    def _save_load_values(self):
        if self._load_capture_running:
            return
        if self._simhub_playing() or self._leveling_running or Grease.greaseActive:
            messagebox.showwarning(
                language_text("MainDialog", "save_load_title"),
                language_text("MainDialog", "save_load_idle_only"),
                parent=self,
            )
            return
        if not messagebox.askokcancel(
            language_text("MainDialog", "save_load_title"),
            language_text("MainDialog", "save_load_confirmation"),
            parent=self,
        ):
            return

        self._load_capture_running = True
        self.save_load_values_button.configure(
            state=tk.DISABLED,
            text=language_text("MainDialog", "save_load_running"),
        )
        self.leveling_start_button.configure(state=tk.DISABLED)
        self.simhub_mode_button.configure(state=tk.DISABLED)
        self.actual_positions_mode_button.configure(state=tk.DISABLED)
        self.center_mode_button.configure(state=tk.DISABLED)
        threading.Thread(
            target=self._save_load_values_worker,
            name="SimRigLoadCapture",
            daemon=True,
        ).start()

    def _save_load_values_worker(self):
        error = None
        calibration = None
        readings = [[] for _ in range(4)]
        try:
            for sample in range(11):
                if self._simhub_playing():
                    raise RuntimeError(
                        language_text("MainDialog", "save_load_runtime_error")
                    )
                for index, axis in enumerate(range(4, 8)):
                    readings[index].append(motion_controller.read_load_rate(axis))
                if sample < 10:
                    time.sleep(0.06)
            load_rates = tuple(median(axis_values) for axis_values in readings)
            calibration = save_load_calibration(load_rates)
        except Exception as load_error:
            error = load_error

        if self._closing:
            return
        try:
            self.after(0, self._finish_save_load_values, calibration, error)
        except tk.TclError:
            pass

    def _finish_save_load_values(self, calibration, error):
        self._load_capture_running = False
        if self._closing or not self.winfo_exists():
            return

        self.save_load_values_button.configure(
            state=tk.NORMAL,
            text=language_text("Panels", "save_load_values"),
        )
        self.simhub_mode_button.configure(state=tk.NORMAL)
        self.actual_positions_mode_button.configure(state=tk.NORMAL)
        self.center_mode_button.configure(state=tk.NORMAL)
        self._update_connection_status()
        if error is not None:
            logger.error("Saving SimRig Load values failed: %s", error)
            messagebox.showerror(
                language_text("MainDialog", "save_load_title"),
                language_text("MainDialog", "save_load_error", error=error),
                parent=self,
            )
            return

        load_text = ", ".join(
            language_text(
                "Formats", "load_item", axis=axis, value=value / 10.0
            )
            for axis, value in zip(range(4, 8), calibration.load_rates, strict=False)
        )
        messagebox.showinfo(
            language_text("MainDialog", "save_load_success_title"),
            language_text(
                "MainDialog",
                "save_load_success",
                load_text=load_text,
                front_rear=calibration.center_of_gravity_front_to_rear_mm,
                left_right=calibration.center_of_gravity_left_to_right_mm,
            ),
            parent=self,
        )

    def _set_motor_row(self, axis, leveling_offset, load_rate, error_state):
        (
            _,
            leveling_offset_label,
            load_rate_label,
            error_state_label,
            _,
        ) = self.motor_value_labels[axis - 1]
        leveling_offset_text = "" if axis in (1, 2, 3) else self._format_leveling_offset(leveling_offset)
        leveling_offset_label.configure(text=leveling_offset_text)
        load_rate_label.configure(text=self._format_load_rate(load_rate))
        error_state_label.configure(text=self._format_error_state(error_state, axis))

    def _clear_load_rate_fields(self):
        for _, _, load_rate_label, _, _ in self.motor_value_labels:
            load_rate_label.configure(text="")

    def _clear_error_state_fields(self, axes):
        for axis in axes:
            _, _, _, error_state_label, _ = self.motor_value_labels[axis - 1]
            error_state_label.configure(text="")

    def _update_leveling_axis_highlight(self):
        fixed_axis = Leveling.levelingFixedAxis if Leveling.levelingActive else None
        for axis, row_labels in enumerate(self.motor_value_labels, start=1):
            row_bg = (
                LEVELING_FIXED_AXIS_BG
                if axis == fixed_axis
                else TABLE_CELL_BG
            )
            for label in row_labels[:-1]:
                label.configure(bg=row_bg)

    def _update_grease_status_cells(self):
        data = Grease.grease_data_snapshot()
        for axis, row_labels in enumerate(self.motor_value_labels, start=1):
            key = str(axis) if axis <= 3 else "4-7"
            playtime = data.get(key, {}).get("playtimeMinutes", 0.0)
            text, background = grease_status(playtime)
            row_labels[-1].configure(text=text, bg=background)

    def _format_leveling_offset(self, value):
        if isinstance(value, (int, float)):
            return language_text("Formats", "leveling_offset", value=value)
        return str(value)

    def _format_load_rate(self, value):
        if isinstance(value, (int, float)):
            return language_text("Formats", "load_rate", value=value / 10)
        return str(value)

    def _format_error_state(self, value, axis=None):
        if (
            axis is not None
            and Leveling.levelingActive
            and axis in Leveling.LEVELING_OFFSET_AXES
        ):
            return language_text("MainDialog", "state_leveling")
        if isinstance(value, int):
            if (
                value == 0
                and axis is not None
                and Grease.greaseActive
                and Grease.greaseAxisFrom is not None
                and Grease.greaseAxisTo is not None
                and Grease.greaseAxisFrom <= axis <= Grease.greaseAxisTo
            ):
                return language_text("MainDialog", "state_greasing")
            return ERROR_STATE_TEXT.get(
                value,
                language_text("MainDialog", "error_unknown", value=value),
            )
        return str(value)

    def _refresh_motor_table(self):
        if self._closing or not self.winfo_exists():
            return

        simhub_playing = self._simhub_playing()
        if simhub_playing:
            self._clear_load_rate_fields()
            self._clear_error_state_fields(range(1, 4))

        polled_axes = (
            range(4, MOTOR_COUNT + 1)
            if simhub_playing
            else range(1, MOTOR_COUNT + 1)
        )
        polled_axes = tuple(polled_axes)

        load_rate_reader = None if simhub_playing else motion_controller.read_load_rate
        error_state_reader = motion_controller.read_error_state
        if (
            polled_axes
            and (load_rate_reader is not None or error_state_reader is not None)
            and not self._motor_refresh_in_progress
            and not self._load_capture_running
            and not self._maintenance_dialog_is_open()
        ):
            axis = next(
                (candidate for candidate in polled_axes if candidate >= self._motor_next_axis),
                polled_axes[0],
            )
            axis_index = polled_axes.index(axis)
            self._motor_next_axis = polled_axes[(axis_index + 1) % len(polled_axes)]
            self._motor_refresh_in_progress = True
            threading.Thread(
                target=self._read_motor_value_worker,
                args=(axis, load_rate_reader, error_state_reader),
                daemon=True,
            ).start()

        axis_refresh_ms = max(
            1, AXIS_STATUS_REFRESH_MS // max(1, len(polled_axes))
        )
        self._motor_after_id = self.after(axis_refresh_ms, self._refresh_motor_table)

    def _read_motor_value_worker(self, axis, load_rate_reader, error_state_reader):
        shcmd = get_simhub_commands_module()
        runtime_state = getattr(shcmd, "runtime_state", None)
        position_lock = getattr(runtime_state, "position_lock", None)
        with position_lock if position_lock is not None else nullcontext():
            leveling_offset = get_leveling_offset(axis)
            if self._simhub_playing():
                load_rate = ""
            else:
                try:
                    load_rate = (
                        load_rate_reader(axis)
                        if load_rate_reader is not None
                        else language_text("Common", "placeholder")
                    )
                except Exception:
                    load_rate = language_text("Common", "placeholder")
            if self._simhub_playing() and axis <= 3:
                error_state = ""
            else:
                try:
                    error_state = (
                        error_state_reader(axis)
                        if error_state_reader is not None
                        else language_text("Common", "placeholder")
                    )
                except Exception:
                    error_state = language_text("Common", "placeholder")
        value = (axis, leveling_offset, load_rate, error_state)

        if self._closing:
            return

        try:
            after_id = self.after(0, self._apply_motor_value, value)
            self._apply_motor_after_ids.add(after_id)
        except tk.TclError:
            pass

    def _apply_motor_value(self, value):
        if self._closing or not self.winfo_exists():
            return

        self._apply_motor_after_ids.clear()
        axis, leveling_offset, load_rate, error_state = value
        if self._simhub_playing():
            load_rate = ""
            if axis <= 3:
                error_state = ""
        self._set_motor_row(axis, leveling_offset, load_rate, error_state)
        self._motor_refresh_in_progress = False

    def _refresh_values(self):
        if self._closing or not self.winfo_exists():
            return

        values = [0] * 7
        current_simhub = get_simhub_module()
        if current_simhub is not None:
            try:
                with current_simhub.latestLock:
                    values = current_simhub.latestValues.copy()
            except Exception:
                values = [0] * 7

        self.bar0.set_value(values[0])
        self.bar1.set_value(values[1])
        self.bar2.set_value(values[2])
        self.bar3.set_value(values[3])
        self.bar4.set_value(values[4])
        self.bar5.set_value(values[5])
        self.bar6.set_value(values[6])

        current_time = datetime.now().strftime("%H:%M:%S")
        self.time_label.config(text=current_time)

        Grease.update_playtime(self._simhub_playing())
        self._update_connection_status()
        self._update_leveling_axis_highlight()
        self._update_grease_status_cells()

        self._refresh_after_id = self.after(REFRESH_MS, self._refresh_values)

    def _refresh_trigger_chart(self):
        if self._closing or not self.winfo_exists():
            return

        now = time.time()
        cutoff = now - TRIGGER_CHART_WINDOW_S
        current_simhub = get_simhub_module()
        if current_simhub is not None:
            interval_lock = getattr(current_simhub, "triggerIntervalLock", None)
            pending_samples = getattr(current_simhub, "triggerIntervalSamples", None)
            if interval_lock is not None and pending_samples is not None:
                try:
                    with interval_lock:
                        # Transfer the complete batch atomically. Samples added
                        # by the sender after this block remain pending for the
                        # next one-second refresh.
                        self._trigger_chart_samples.extend(pending_samples)
                        pending_samples.clear()
                except Exception:
                    logger.exception("Could not transfer trigger chart samples")

        while (
            self._trigger_chart_samples
            and self._trigger_chart_samples[0][0] < cutoff
        ):
            self._trigger_chart_samples.popleft()

        self.trigger_interval_chart.set_samples(
            list(self._trigger_chart_samples),
            now,
        )
        self._trigger_chart_after_id = self.after(
            TRIGGER_CHART_REFRESH_MS,
            self._refresh_trigger_chart,
        )


def run_standalone():
    """Run the debug dialog with its own logging and ModBus cleanup."""
    log_file = SCRIPT_DIR / "LOG" / "simhub2a6.log"
    configure_logging(log_file)
    logger.info("Starting dialog directly; logging to %s", log_file)

    root = Dialog()
    root.resizable(True, True)
    closing = False

    def on_close():
        nonlocal closing
        if closing:
            return
        closing = True
        root.close()
        shcmd = get_simhub_commands_module()
        if shcmd is not None and motion_controller.connected:
            try:
                shcmd.handle_end()
            except Exception:
                logger.exception("Standalone dialog ModBus cleanup failed")

    root.protocol("WM_DELETE_WINDOW", on_close)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        logger.info("Standalone dialog interrupted by Ctrl+C")
    finally:
        on_close()


if __name__ == "__main__":
    run_standalone()
