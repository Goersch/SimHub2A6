#!/usr/bin/env python3

import threading
import tkinter as tk
from datetime import datetime
from tkinter import font, messagebox
from typing import cast

from . import Grease
from .LIB.config import CONTROL_CONFIG, GREASE_CONFIG, SIMHUB_CONFIG
from .LIB.language import text as language_text
from .LIB.logging_config import get_logger
from .LIB.ui_panels import GreasePanel

logger = get_logger("ui.maintenance")

HUB_AXIS_FROM = CONTROL_CONFIG.hub_axis_from
HUB_AXIS_TO = CONTROL_CONFIG.hub_axis_to
FRONT_AXIS = 1
MIDDLE_AXIS = 2
REAR_AXIS = 3
RAW_POSITION_MIN = SIMHUB_CONFIG.position_min
RAW_POSITION_CENTER = (RAW_POSITION_MIN + SIMHUB_CONFIG.position_max) // 2
RAW_POSITION_MAX = SIMHUB_CONFIG.position_max
HOMING_STATUS_REFRESH_MS = 1000
AXIS_GROUP_NAMES = (
    language_text("Common", "axis_front"),
    language_text("Common", "axis_middle"),
    language_text("Common", "axis_rear"),
    language_text("Common", "axis_hub"),
)
ACTION_RIGHT = language_text("Maintenance", "action_right")
ACTION_CENTER = language_text("Maintenance", "action_center")
ACTION_LEFT = language_text("Maintenance", "action_left")
ACTION_HOME = language_text("Maintenance", "action_home")
ACTION_FRONT = language_text("Maintenance", "action_front_position")
ACTION_REAR = language_text("Maintenance", "action_rear_position")
ACTION_TOP = language_text("Maintenance", "action_top_position")
ACTION_BOTTOM = language_text("Maintenance", "action_bottom_position")
TABLE_HEADER_BG = "#4A6D92"
TABLE_HEADER_FG = "#FFFFFF"
TABLE_CELL_BG = "#FFFFFF"
TABLE_CELL_FG = "#111111"
TABLE_WARNING_BG = "#FFF3A3"
TABLE_ERROR_BG = "#F8B4B4"


def _running_action_text(axis_name, action_name):
    movement_text = {
        ACTION_LEFT: language_text("Maintenance", "moving_left"),
        ACTION_RIGHT: language_text("Maintenance", "moving_right"),
        ACTION_CENTER: language_text("Maintenance", "moving_center"),
        ACTION_FRONT: language_text("Maintenance", "moving_front"),
        ACTION_REAR: language_text("Maintenance", "moving_rear"),
        ACTION_TOP: language_text("Maintenance", "moving_top"),
        ACTION_BOTTOM: language_text("Maintenance", "moving_bottom"),
        ACTION_HOME: language_text("Maintenance", "homing_running"),
    }.get(
        action_name,
        language_text("Maintenance", "action_running", action_name=action_name),
    )
    return f"{axis_name}: {movement_text}"


class WartungDialog(tk.Toplevel):
    def __init__(self, parent, commands, is_simhub_connected, on_close=None):
        super().__init__(parent)
        self._commands = commands
        self._is_simhub_connected = is_simhub_connected
        self._on_close = on_close
        self._closing = False
        self._busy = False
        self._buttons = []
        self._position_buttons = {
            "front_position": [],
            "middle_position": [],
            "rear_position": [],
            "hub_position": [],
        }
        self._homing_buttons = []
        self.grease_buttons = []
        self.grease_info_labels = []
        self._grease_active_range: tuple[int, int] | None = None
        self._grease_stopping_range: tuple[int, int] | None = None
        self._front_homed = False
        self._hub_homed = False
        self._middle_homed = False
        self._rear_homed = False
        self._connected = None
        self._initializing = True
        self._ready = False
        self._status_read_in_progress = False
        self._status_after_id = None

        self.title(language_text("Maintenance", "title"))
        self.resizable(True, True)
        self.minsize(420, 320)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.transient(parent)

        content = tk.Frame(self, padx=20, pady=18)
        content.pack(fill="both", expand=True)
        for column in range(4):
            content.columnconfigure(column, weight=1)
        button_font = font.Font(family="Segoe UI", size=9, weight="bold")

        for column, axis_name in enumerate(AXIS_GROUP_NAMES):
            tk.Label(
                content,
                text=axis_name,
                font=button_font,
                anchor="center",
            ).grid(row=0, column=column, padx=6, pady=(0, 8), sticky="ew")

        actions = (
            (
                ACTION_RIGHT,
                lambda: commands.move_axes_for_maintenance(
                    FRONT_AXIS, FRONT_AXIS, RAW_POSITION_MAX
                ),
                "front_position",
                1,
                0,
            ),
            (
                ACTION_CENTER,
                lambda: commands.move_axes_for_maintenance(
                    FRONT_AXIS, FRONT_AXIS, RAW_POSITION_CENTER
                ),
                "front_position",
                2,
                0,
            ),
            (
                ACTION_LEFT,
                lambda: commands.move_axes_for_maintenance(
                    FRONT_AXIS, FRONT_AXIS, RAW_POSITION_MIN
                ),
                "front_position",
                3,
                0,
            ),
            (
                ACTION_HOME,
                commands.home_front_axis_for_maintenance,
                "homing",
                4,
                0,
            ),
            (
                ACTION_FRONT,
                lambda: commands.move_axes_for_maintenance(
                    MIDDLE_AXIS, MIDDLE_AXIS, RAW_POSITION_MAX
                ),
                "middle_position",
                1,
                1,
            ),
            (
                ACTION_CENTER,
                lambda: commands.move_axes_for_maintenance(
                    MIDDLE_AXIS, MIDDLE_AXIS, RAW_POSITION_CENTER
                ),
                "middle_position",
                2,
                1,
            ),
            (
                ACTION_REAR,
                lambda: commands.move_axes_for_maintenance(
                    MIDDLE_AXIS, MIDDLE_AXIS, RAW_POSITION_MIN
                ),
                "middle_position",
                3,
                1,
            ),
            (
                ACTION_HOME,
                commands.home_middle_axis_for_maintenance,
                "homing",
                4,
                1,
            ),
            (
                ACTION_RIGHT,
                lambda: commands.move_axes_for_maintenance(
                    REAR_AXIS, REAR_AXIS, RAW_POSITION_MAX
                ),
                "rear_position",
                1,
                2,
            ),
            (
                ACTION_CENTER,
                lambda: commands.move_axes_for_maintenance(
                    REAR_AXIS, REAR_AXIS, RAW_POSITION_CENTER
                ),
                "rear_position",
                2,
                2,
            ),
            (
                ACTION_LEFT,
                lambda: commands.move_axes_for_maintenance(
                    REAR_AXIS, REAR_AXIS, RAW_POSITION_MIN
                ),
                "rear_position",
                3,
                2,
            ),
            (
                ACTION_HOME,
                commands.home_rear_axis_for_maintenance,
                "homing",
                4,
                2,
            ),
            (
                ACTION_TOP,
                lambda: commands.move_axes_for_maintenance(
                    HUB_AXIS_FROM, HUB_AXIS_TO, RAW_POSITION_MAX
                ),
                "hub_position",
                1,
                3,
            ),
            (
                ACTION_CENTER,
                lambda: commands.move_axes_for_maintenance(
                    HUB_AXIS_FROM, HUB_AXIS_TO, RAW_POSITION_CENTER
                ),
                "hub_position",
                2,
                3,
            ),
            (
                ACTION_BOTTOM,
                lambda: commands.move_axes_for_maintenance(
                    HUB_AXIS_FROM, HUB_AXIS_TO, RAW_POSITION_MIN
                ),
                "hub_position",
                3,
                3,
            ),
            (
                ACTION_HOME,
                commands.home_hub_axes_for_maintenance,
                "homing",
                4,
                3,
            ),
        )

        for label, action, group, row, column in actions:
            button = tk.Button(
                content,
                text=label,
                font=button_font,
                width=20,
                command=lambda name=label, command=action, action_group=group,
                axis_name=AXIS_GROUP_NAMES[column]: self._run_action(
                    axis_name, name, command, action_group
                ),
            )
            button.grid(
                row=row,
                column=column,
                padx=6,
                pady=(0, 7),
                sticky="ew",
            )
            self._buttons.append(button)
            if group in self._position_buttons:
                self._position_buttons[group].append(button)
            else:
                self._homing_buttons.append(button)

        colors = (TABLE_HEADER_BG, TABLE_HEADER_FG, TABLE_CELL_BG, TABLE_CELL_FG)
        self.grease_table_frame = GreasePanel(content)
        self.grease_table_frame.grid(
            row=5,
            column=0,
            columnspan=4,
            padx=6,
            pady=(12, 0),
            sticky="nsew",
        )
        self.grease_buttons, self.grease_info_labels = (
            self.grease_table_frame.build_table(
                Grease.GREASE_ROWS,
                lambda start, end: self._set_grease(start, end, 1),
                lambda start, end: self._set_grease(start, end, 0),
                self._reset_grease_data,
                colors,
            )
        )
        self._update_grease_info_labels()

        self.status_label = tk.Label(
            content,
            text=language_text("Maintenance", "ready"),
            anchor="w",
            fg="#555555",
        )
        self.status_label.grid(
            row=6, column=0, columnspan=4, padx=6, pady=(12, 0), sticky="ew"
        )
        self.set_simhub_connected(self._is_simhub_connected())
        self._sync_grease_state()
        self.after_idle(self._start_initialization)
        self.after_idle(self._center_on_parent)

    def _center_on_parent(self):
        if self._closing or not self.winfo_exists():
            return
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = self.master.winfo_rootx() + max(0, (self.master.winfo_width() - width) // 2)
        y = self.master.winfo_rooty() + max(0, (self.master.winfo_height() - height) // 2)
        self.geometry(f"+{x}+{y}")
        self.lift()
        self.focus_force()

    def set_simhub_connected(self, connected):
        connected = bool(connected)
        connection_changed = connected != self._connected
        self._connected = connected
        self._apply_button_states()
        if connected and not self._busy and connection_changed:
            if self._status_after_id is not None:
                try:
                    self.after_cancel(self._status_after_id)
                except tk.TclError:
                    pass
                self._status_after_id = None
            self.status_label.configure(
                text=language_text("Maintenance", "simhub_locked")
            )
        elif not connected and not self._busy and connection_changed:
            if self._initializing:
                self.status_label.configure(
                    text=language_text("Maintenance", "initializing")
                )
            elif self._ready:
                self.status_label.configure(
                    text=language_text("Maintenance", "reading_homing")
                )
                self._refresh_homing_status()

    def _apply_button_states(self):
        grease_running = self._grease_active_range is not None
        if self._connected or self._busy or grease_running or not self._ready:
            for button in self._buttons:
                button.configure(state=tk.DISABLED)
            self._update_grease_buttons()
            return

        for button in self._homing_buttons:
            button.configure(state=tk.NORMAL)
        homing_states = {
            "front_position": self._front_homed,
            "middle_position": self._middle_homed,
            "rear_position": self._rear_homed,
            "hub_position": self._hub_homed,
        }
        for group, buttons in self._position_buttons.items():
            state = tk.NORMAL if homing_states[group] else tk.DISABLED
            for button in buttons:
                button.configure(state=state)
        self._update_grease_buttons()

    def _start_initialization(self):
        if self._closing:
            return
        self._apply_button_states()
        threading.Thread(
            target=self._initialization_worker,
            name="A6MaintenanceInitialization",
            daemon=True,
        ).start()

    def _initialization_worker(self):
        error = None
        try:
            self._commands.ensure_maintenance_initialized()
        except Exception as exc:
            error = exc
            logger.exception("Maintenance initialization failed")

        if self._closing:
            return
        try:
            self.after(0, self._finish_initialization, error)
        except tk.TclError:
            pass

    def _finish_initialization(self, error):
        self._initializing = False
        self._ready = error is None
        if self._closing or not self.winfo_exists():
            return
        if error is not None:
            self.status_label.configure(
                text=language_text("Maintenance", "initialization_failed")
            )
            self._apply_button_states()
            messagebox.showerror(
                language_text("Maintenance", "title"),
                language_text("Maintenance", "initialization_error", error=error),
                parent=self,
            )
            return
        if self._connected:
            self.status_label.configure(
                text=language_text("Maintenance", "simhub_locked")
            )
            self._apply_button_states()
            return
        self.status_label.configure(
            text=language_text("Maintenance", "reading_homing")
        )
        self._apply_button_states()
        self._refresh_homing_status()

    def _refresh_homing_status(self):
        if (
            self._closing
            or self._connected
            or self._busy
            or self._grease_active_range is not None
            or not self._ready
            or self._status_read_in_progress
        ):
            return
        self._status_read_in_progress = True
        threading.Thread(
            target=self._homing_status_worker,
            name="A6MaintenanceHomingStatus",
            daemon=True,
        ).start()

    def _schedule_homing_status_refresh(self):
        if (
            self._closing
            or self._connected
            or not self._ready
            or self._status_after_id is not None
        ):
            return
        self._status_after_id = self.after(
            HOMING_STATUS_REFRESH_MS,
            self._run_scheduled_homing_status_refresh,
        )

    def _run_scheduled_homing_status_refresh(self):
        self._status_after_id = None
        self._sync_grease_state()
        if self._grease_active_range is not None:
            self._schedule_homing_status_refresh()
            return
        self._refresh_homing_status()

    def _format_last_grease(self, value):
        if not value:
            return language_text("Common", "placeholder")
        try:
            return datetime.fromisoformat(value).strftime(
                language_text("Formats", "last_grease")
            )
        except ValueError:
            return str(value)

    @staticmethod
    def _format_playtime(value):
        try:
            total_minutes = int(max(0.0, float(value)))
        except (TypeError, ValueError):
            total_minutes = 0
        return language_text(
            "Formats",
            "playtime",
            hours=total_minutes // 60,
            minutes=total_minutes % 60,
        )

    @staticmethod
    def _playtime_bg(value):
        try:
            hours = max(0.0, float(value)) / 60.0
        except (TypeError, ValueError):
            hours = 0.0
        if hours > GREASE_CONFIG.alarm_after_operating_hours:
            return TABLE_ERROR_BG
        if hours > GREASE_CONFIG.warning_after_operating_hours:
            return TABLE_WARNING_BG
        return TABLE_CELL_BG

    def _update_grease_info_labels(self):
        data = Grease.grease_data_snapshot()
        for key, last_grease_label, playtime_label in self.grease_info_labels:
            entry = data.get(key, {})
            playtime = entry.get("playtimeMinutes", 0.0)
            last_grease_label.configure(
                text=self._format_last_grease(entry.get("lastGreaseAt", ""))
            )
            playtime_label.configure(
                text=self._format_playtime(playtime),
                bg=self._playtime_bg(playtime),
            )

    def _reset_grease_data(self, axis_from, axis_to):
        Grease.reset_grease_data(axis_from, axis_to)
        self._update_grease_info_labels()

    @staticmethod
    def _grease_axis_name(axis_from, axis_to):
        for row_from, row_to, name in Grease.GREASE_ROWS:
            if (row_from, row_to) == (axis_from, axis_to):
                return name
        return language_text(
            "Maintenance", "axes_range", axis_from=axis_from, axis_to=axis_to
        )

    def _grease_row_homed(self, axis_from, axis_to):
        return {
            (1, 1): self._front_homed,
            (2, 2): self._middle_homed,
            (3, 3): self._rear_homed,
            (HUB_AXIS_FROM, HUB_AXIS_TO): self._hub_homed,
        }.get((axis_from, axis_to), False)

    def _update_grease_buttons(self):
        grease_running = self._grease_active_range is not None
        for axis_from, axis_to, start_button, stop_button in self.grease_buttons:
            row = (axis_from, axis_to)
            is_active = self._grease_active_range == row
            start_blocked = (
                self._connected
                or self._busy
                or grease_running
                or not self._ready
                or not self._grease_row_homed(axis_from, axis_to)
            )
            start_button.configure(
                state=tk.DISABLED if start_blocked else tk.NORMAL
            )
            stop_requested = self._grease_stopping_range == row
            stop_button.configure(
                state=tk.NORMAL if is_active and not stop_requested else tk.DISABLED
            )

    def _set_grease(self, axis_from, axis_to, status):
        row = (axis_from, axis_to)
        axis_name = self._grease_axis_name(axis_from, axis_to)
        if status:
            if (
                self._busy
                or self._grease_active_range is not None
                or self._connected
                or not self._ready
                or not self._grease_row_homed(axis_from, axis_to)
            ):
                return
            self._grease_active_range = row
            self._grease_stopping_range = None
            Grease.grease(axis_from, axis_to, 1)
            self.status_label.configure(
                text=language_text("Maintenance", "greasing", axis_name=axis_name)
            )
        else:
            if self._grease_active_range != row:
                return
            self._grease_stopping_range = row
            Grease.grease(axis_from, axis_to, 0)
            self.status_label.configure(
                text=language_text(
                    "Maintenance", "greasing_stopping", axis_name=axis_name
                )
            )
        self._apply_button_states()
        self._schedule_homing_status_refresh()

    def _sync_grease_state(self):
        previous_range = self._grease_active_range
        if Grease.greaseActive:
            self._grease_active_range = (
                cast(int, Grease.greaseAxisFrom),
                cast(int, Grease.greaseAxisTo),
            )
            if previous_range != self._grease_active_range:
                axis_name = self._grease_axis_name(*self._grease_active_range)
                self.status_label.configure(
                    text=language_text(
                        "Maintenance", "greasing", axis_name=axis_name
                    )
                )
        else:
            self._grease_active_range = None
            self._grease_stopping_range = None
            if previous_range is not None:
                axis_name = self._grease_axis_name(*previous_range)
                if Grease.greaseLastError is None:
                    self.status_label.configure(
                        text=language_text(
                            "Maintenance", "greasing_finished", axis_name=axis_name
                        )
                    )
                else:
                    self.status_label.configure(
                        text=language_text(
                            "Maintenance", "greasing_failed", axis_name=axis_name
                        )
                    )
        self._update_grease_info_labels()
        self._apply_button_states()

    def _homing_status_worker(self):
        status = None
        error = None
        try:
            status = self._commands.maintenance_homing_status()
        except Exception as exc:
            error = exc
            logger.warning("Maintenance homing status could not be read: %s", exc)

        if self._closing:
            return
        try:
            self.after(0, self._finish_homing_status, status, error)
        except tk.TclError:
            pass

    def _finish_homing_status(self, status, error):
        self._status_read_in_progress = False
        if self._closing or not self.winfo_exists():
            return
        if error is None:
            (
                self._front_homed,
                self._middle_homed,
                self._rear_homed,
                self._hub_homed,
            ) = status
            homing_done = language_text("Maintenance", "homing_done")
            not_homed = language_text("Maintenance", "not_homed")
            front_text = homing_done if self._front_homed else not_homed
            hub_text = homing_done if self._hub_homed else not_homed
            middle_text = homing_done if self._middle_homed else not_homed
            rear_text = homing_done if self._rear_homed else not_homed
            if self._connected:
                self.status_label.configure(
                    text=language_text("Maintenance", "simhub_locked")
                )
            elif self._grease_active_range is None:
                self.status_label.configure(
                    text=language_text(
                        "Maintenance",
                        "homing_summary",
                        front=front_text,
                        middle=middle_text,
                        rear=rear_text,
                        hub=hub_text,
                    )
                )
        else:
            self._front_homed = False
            self._hub_homed = False
            self._middle_homed = False
            self._rear_homed = False
            if self._grease_active_range is None:
                self.status_label.configure(
                    text=language_text("Maintenance", "homing_read_error")
                )
        self._apply_button_states()
        self._schedule_homing_status_refresh()

    def _run_action(self, axis_name, name, action, group):
        if (
            not self._ready
            or self._busy
            or self._grease_active_range is not None
            or self._is_simhub_connected()
        ):
            self.set_simhub_connected(self._is_simhub_connected())
            return
        homing_states = {
            "front_position": self._front_homed,
            "middle_position": self._middle_homed,
            "rear_position": self._rear_homed,
            "hub_position": self._hub_homed,
        }
        if group in homing_states and not homing_states[group]:
            return

        self._busy = True
        self.status_label.configure(text=_running_action_text(axis_name, name))
        self._apply_button_states()
        threading.Thread(
            target=self._action_worker,
            args=(axis_name, name, action),
            name="A6MaintenanceAction",
            daemon=True,
        ).start()

    def _action_worker(self, axis_name, name, action):
        error = None
        homing_status = None
        try:
            action()
        except Exception as exc:
            error = exc
            logger.exception("Maintenance action failed: %s", name)
        try:
            homing_status = self._commands.maintenance_homing_status()
        except Exception as exc:
            logger.warning("Homing status after maintenance action failed: %s", exc)

        if self._closing:
            return
        try:
            self.after(
                0,
                self._finish_action,
                axis_name,
                name,
                error,
                homing_status,
            )
        except tk.TclError:
            pass

    def _finish_action(self, axis_name, name, error, homing_status):
        self._busy = False
        if self._closing or not self.winfo_exists():
            return
        if homing_status is not None:
            (
                self._front_homed,
                self._middle_homed,
                self._rear_homed,
                self._hub_homed,
            ) = homing_status
        if error is None:
            self.status_label.configure(
                text=language_text(
                    "Maintenance",
                    "action_finished",
                    axis_name=axis_name,
                    action_name=name,
                )
            )
        else:
            self.status_label.configure(
                text=language_text(
                    "Maintenance",
                    "action_failed",
                    axis_name=axis_name,
                    action_name=name,
                )
            )
            messagebox.showerror(
                language_text("Maintenance", "title"),
                language_text("Maintenance", "action_error", error=error),
                parent=self,
            )
        self.set_simhub_connected(self._is_simhub_connected())
        self._apply_button_states()
        self._schedule_homing_status_refresh()

    def close(self):
        if self._closing:
            return
        self._closing = True
        if self._grease_active_range is not None:
            Grease.grease(*self._grease_active_range, 0)
        if self._status_after_id is not None:
            try:
                self.after_cancel(self._status_after_id)
            except tk.TclError:
                pass
            self._status_after_id = None
        if self.winfo_exists():
            self.destroy()
        if self._on_close is not None:
            self._on_close()
