#!/usr/bin/env python3

import csv
import math
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import font

from .LIB.config import SIMHUB_CONFIG
from .LIB.language import text as language_text
from .LIB.logging_config import get_logger

logger = get_logger("analysis")


analyseActive = False
SIMHUB_DATA_DIR = Path(__file__).resolve().parent / "SimHubData"
AXIS_LABELS = (
    language_text("Analysis", "axis_1"),
    language_text("Analysis", "axis_2"),
    language_text("Analysis", "axis_3"),
    language_text("Analysis", "axis_4"),
    language_text("Analysis", "axis_5"),
    language_text("Analysis", "axis_6"),
    language_text("Analysis", "axis_7"),
)
AXIS_COLORS = (
    "#D62728",
    "#FF7F0E",
    "#2CA02C",
    "#1F77B4",
    "#9467BD",
    "#8C564B",
    "#17BECF",
)
RAW_VALUE_MAX = SIMHUB_CONFIG.position_max
SIGNAL_TARGET = "target"
SIGNAL_CALCULATED_ACTUAL = "calculated_actual"
SIGNAL_ACTUAL = "actual"
SIGNAL_GROUND_DEVIATION = "ground_deviation"
SIGNAL_STYLES = {
    SIGNAL_TARGET: {"dash": (), "width": 1.5, "color_mix": None},
    SIGNAL_CALCULATED_ACTUAL: {
        "dash": (7, 3),
        "width": 1.8,
        "color_mix": ("#FFFFFF", 0.42),
    },
    SIGNAL_ACTUAL: {
        "dash": (3, 3),
        "width": 2.2,
        "color_mix": ("#000000", 0.28),
    },
    SIGNAL_GROUND_DEVIATION: {
        "dash": (10, 3, 2, 3),
        "width": 2.0,
        "color_mix": None,
    },
}
HUB_AXIS_INDEXES = frozenset(range(3, 7))
RESIZE_REDRAW_DELAY_MS = 100


def _recording_sort_key(file_path):
    try:
        recorded_at = datetime.strptime(file_path.stem[:19], "%Y-%m-%d_%H-%M-%S")
        timestamp = recorded_at.timestamp()
    except (ValueError, OSError):
        try:
            timestamp = file_path.stat().st_mtime
        except OSError:
            timestamp = 0
    return timestamp, file_path.stem.lower()


def get_simhub_data_files():
    try:
        files = [file_path for file_path in SIMHUB_DATA_DIR.iterdir() if file_path.is_file()]
    except OSError:
        return []
    return sorted(files, key=_recording_sort_key, reverse=True)


def raw_value_to_mm(value):
    return float(value) / RAW_VALUE_MAX * 200.0 - 100.0


def _mix_color(color, mix):
    if mix is None:
        return color
    mix_color, fraction = mix
    base_rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    mix_rgb = tuple(int(mix_color[index:index + 2], 16) for index in (1, 3, 5))
    result = tuple(
        round(base + (target - base) * fraction)
        for base, target in zip(base_rgb, mix_rgb, strict=False)
    )
    return "#" + "".join(f"{channel:02X}" for channel in result)


def _parse_recording_row(row):
    timestamp = datetime.fromisoformat(row["Timestamp"]).timestamp()
    target_values = tuple(
        float(row[f"TargetPosition{axis}"])
        if row.get(f"TargetPosition{axis}") not in (None, "")
        else raw_value_to_mm(row[f"Value{axis}"])
        for axis in range(1, 8)
    )
    new_format = "CalculatedActualPosition1" in row
    calculated_actual_values = []
    read_actual_values = []
    for axis, target_value in enumerate(target_values, start=1):
        calculated_value = row.get(
            f"CalculatedActualPosition{axis}"
            if new_format
            else f"ActualPosition{axis}"
        )
        try:
            calculated_value = float(calculated_value)
        except (TypeError, ValueError):
            calculated_value = target_value
        calculated_actual_values.append(calculated_value)

        read_value = row.get(f"ActualPosition{axis}") if new_format else 0.0
        try:
            read_value = float(read_value)
        except (TypeError, ValueError):
            read_value = 0.0
        read_actual_values.append(read_value)
    calculated_actual_values = tuple(calculated_actual_values)
    read_actual_values = tuple(read_actual_values)
    ground_deviations = (0.0, 0.0, 0.0) + tuple(
        float(
            row.get(f"GroundDeviation{axis}")
            or row.get(f"GroundDistance{axis}")
            or 0.0
        )
        for axis in range(4, 8)
    )
    return (
        timestamp,
        {
            SIGNAL_TARGET: target_values,
            SIGNAL_CALCULATED_ACTUAL: calculated_actual_values,
            SIGNAL_ACTUAL: read_actual_values,
            SIGNAL_GROUND_DEVIATION: ground_deviations,
        },
    )


def load_recording(file_path, max_points=6000):
    try:
        estimated_rows = max(1, file_path.stat().st_size // 70)
    except OSError:
        estimated_rows = max_points
    sample_step = max(1, math.ceil(estimated_rows / max_points))

    samples = []
    last_row = None
    with file_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file, delimiter=";")
        for row_index, row in enumerate(reader):
            last_row = row
            if row_index % sample_step != 0:
                continue
            try:
                samples.append(_parse_recording_row(row))
            except (KeyError, TypeError, ValueError):
                continue

    if last_row is not None:
        try:
            last_sample = _parse_recording_row(last_row)
        except (KeyError, TypeError, ValueError):
            last_sample = None
        if last_sample is not None and (not samples or samples[-1] != last_sample):
            samples.append(last_sample)
    if len(samples) > max_points:
        final_sample = samples[-1]
        reduction_step = math.ceil(len(samples) / max_points)
        samples = samples[::reduction_step]
        if samples[-1] != final_sample:
            samples.append(final_sample)
    return samples


class SignalChart(tk.Canvas):
    def __init__(self, parent):
        super().__init__(
            parent,
            bg="#FFFFFF",
            highlightthickness=1,
            highlightbackground="#B7C6D8",
        )
        self.samples = []
        self.selected_axes = [True] * 7
        self.selected_signals = {
            SIGNAL_TARGET: True,
            SIGNAL_CALCULATED_ACTUAL: False,
            SIGNAL_ACTUAL: False,
            SIGNAL_GROUND_DEVIATION: False,
        }
        self.message = language_text("Analysis", "no_data")
        self._redraw_after_id = None
        self._last_configure_size = None
        self._view_time_from = None
        self._view_time_to = None
        self._view_value_from = -100.0
        self._view_value_to = 100.0
        self._plot_rect: tuple[float, float, float, float] | None = None
        self._drag_start: float | None = None
        self._zoom_rectangle: int | None = None
        self.bind("<Configure>", self._schedule_redraw)
        self.bind("<ButtonPress-1>", self._start_zoom)
        self.bind("<B1-Motion>", self._drag_zoom)
        self.bind("<ButtonRelease-1>", self._finish_zoom)
        self.bind("<Button-3>", self._reset_zoom)

    def set_message(self, message):
        self.samples = []
        self.message = message
        self.draw()

    def set_data(self, samples):
        self.samples = samples
        self.message = language_text("Analysis", "no_data") if not samples else ""
        self._reset_view()
        self.draw()

    def set_selected_axes(self, selected_axes):
        self.selected_axes = list(selected_axes)
        self.draw()

    def set_selected_signals(self, selected_signals):
        self.selected_signals = dict(selected_signals)
        self.draw()

    def _schedule_redraw(self, _event=None):
        if _event is not None:
            configure_size = (_event.width, _event.height)
            if configure_size == self._last_configure_size:
                return
            self._last_configure_size = configure_size
        if self._redraw_after_id is not None:
            self.after_cancel(self._redraw_after_id)
        # A redraw can contain several thousand line coordinates.  Using
        # after_idle here redraws for nearly every intermediate size on
        # Windows and makes interactive resizing appear to hang.
        self._redraw_after_id = self.after(
            RESIZE_REDRAW_DELAY_MS,
            self._run_scheduled_redraw,
        )

    def _run_scheduled_redraw(self):
        self._redraw_after_id = None
        self.draw()

    def _reset_view(self):
        self._view_time_from = None
        self._view_time_to = None
        self._view_value_from = -100.0
        self._view_value_to = 100.0
        self._drag_start = None
        self._zoom_rectangle = None

    def _point_in_plot(self, x, y):
        plot_rect = self._plot_rect
        if plot_rect is None:
            return False
        left, top, right, bottom = plot_rect
        return left <= x <= right and top <= y <= bottom

    def _clamp_to_plot(self, x, y):
        plot_rect = self._plot_rect
        if plot_rect is None:
            return x, y
        left, top, right, bottom = plot_rect
        return (
            max(left, min(right, x)),
            max(top, min(bottom, y)),
        )

    def _start_zoom(self, event):
        plot_rect = self._plot_rect
        if (
            not self.samples
            or plot_rect is None
            or not self._point_in_plot(event.x, event.y)
        ):
            return
        _, top, _, bottom = plot_rect
        self._drag_start = event.x
        if self._zoom_rectangle is not None:
            self.delete(self._zoom_rectangle)
        self._zoom_rectangle = self.create_rectangle(
            event.x,
            top,
            event.x,
            bottom,
            outline="#0078D4",
            width=2,
            dash=(5, 3),
        )

    def _drag_zoom(self, event):
        drag_start = self._drag_start
        plot_rect = self._plot_rect
        zoom_rectangle = self._zoom_rectangle
        if drag_start is None or plot_rect is None or zoom_rectangle is None:
            return
        x, _ = self._clamp_to_plot(event.x, event.y)
        _, top, _, bottom = plot_rect
        self.coords(zoom_rectangle, drag_start, top, x, bottom)

    def _finish_zoom(self, event):
        start_x = self._drag_start
        plot_rect = self._plot_rect
        if start_x is None or plot_rect is None:
            return

        end_x, _ = self._clamp_to_plot(event.x, event.y)
        self._drag_start = None
        if self._zoom_rectangle is not None:
            self.delete(self._zoom_rectangle)
            self._zoom_rectangle = None

        if abs(end_x - start_x) < 6:
            return

        left, _, right, _ = plot_rect
        current_time_from, current_time_to = self._visible_time_range()

        selection_left = min(start_x, end_x)
        selection_right = max(start_x, end_x)

        self._view_time_from = current_time_from + (
            (selection_left - left) / (right - left)
        ) * (current_time_to - current_time_from)
        self._view_time_to = current_time_from + (
            (selection_right - left) / (right - left)
        ) * (current_time_to - current_time_from)
        self._view_value_from = -100.0
        self._view_value_to = 100.0
        self.draw()

    def _reset_zoom(self, _event=None):
        if not self.samples:
            return
        self._reset_view()
        self.draw()
        return "break"

    def _visible_time_range(self):
        full_start = self.samples[0][0]
        full_end = self.samples[-1][0]
        start = full_start if self._view_time_from is None else self._view_time_from
        end = full_end if self._view_time_to is None else self._view_time_to
        if end <= start:
            end = start + 0.001
        return start, end

    def draw(self):
        if not self.winfo_exists():
            return

        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 160 or height < 120:
            return

        left, right, top, bottom = 58, 16, 16, 44
        plot_left = left
        plot_right = width - right
        plot_top = top
        plot_bottom = height - bottom
        plot_width = max(1, plot_right - plot_left)
        plot_height = max(1, plot_bottom - plot_top)
        self._plot_rect = (plot_left, plot_top, plot_right, plot_bottom)

        value_from = self._view_value_from
        value_to = self._view_value_to
        value_span = max(0.001, value_to - value_from)
        for tick in range(5):
            fraction = tick / 4
            value = value_to - fraction * value_span
            y = plot_top + fraction * plot_height
            self.create_line(plot_left, y, plot_right, y, fill="#D9E0E7")
            value_label = f"{value:.1f}" if value_span < 20 else f"{value:.0f}"
            self.create_text(plot_left - 7, y, text=value_label, anchor="e", fill="#333333")

        self.create_line(plot_left, plot_top, plot_left, plot_bottom, fill="#333333")
        self.create_line(plot_left, plot_bottom, plot_right, plot_bottom, fill="#333333")
        self.create_text(
            13,
            (plot_top + plot_bottom) / 2,
            text=language_text("Common", "unit_mm"),
            angle=90,
        )
        self.create_text(
            (plot_left + plot_right) / 2,
            height - 9,
            text=language_text("Analysis", "time_axis"),
        )

        if not self.samples:
            self.create_text(
                (plot_left + plot_right) / 2,
                (plot_top + plot_bottom) / 2,
                text=self.message,
                fill="#666666",
            )
            return

        start_time, end_time = self._visible_time_range()
        time_span = end_time - start_time
        for tick in range(5):
            fraction = tick / 4
            x = plot_left + fraction * plot_width
            tick_time = start_time + fraction * time_span
            time_format = "%H:%M:%S.%f" if time_span < 1 else "%H:%M:%S"
            label = datetime.fromtimestamp(tick_time).strftime(time_format)
            if time_span < 1:
                label = label[:-3]
            self.create_line(x, plot_bottom, x, plot_bottom + 4, fill="#333333")
            self.create_text(x, plot_bottom + 7, text=label, anchor="n", fill="#333333")

        visible_samples = [
            sample
            for sample in self.samples
            if start_time <= sample[0] <= end_time
        ]
        max_draw_points = max(200, int(plot_width * 2))
        draw_step = max(1, math.ceil(len(visible_samples) / max_draw_points))
        draw_samples = visible_samples[::draw_step]
        if (
            visible_samples
            and draw_samples
            and draw_samples[-1] is not visible_samples[-1]
        ):
            draw_samples.append(visible_samples[-1])

        for signal_name, signal_selected in self.selected_signals.items():
            if not signal_selected:
                continue
            style = SIGNAL_STYLES[signal_name]
            for axis_index, axis_selected in enumerate(self.selected_axes):
                if not axis_selected:
                    continue
                if (
                    signal_name == SIGNAL_GROUND_DEVIATION
                    and axis_index not in HUB_AXIS_INDEXES
                ):
                    continue
                coordinates = []
                for timestamp, signal_values in draw_samples:
                    x = plot_left + (timestamp - start_time) / time_span * plot_width
                    value = signal_values[signal_name][axis_index]
                    value = max(value_from, min(value_to, value))
                    y = plot_top + (value_to - value) / value_span * plot_height
                    coordinates.extend((x, y))
                if len(coordinates) >= 4:
                    self.create_line(
                        *coordinates,
                        fill=_mix_color(
                            AXIS_COLORS[axis_index], style["color_mix"]
                        ),
                        width=style["width"],
                        dash=style["dash"],
                    )


class AnalyseDialog(tk.Toplevel):
    def __init__(self, parent, on_close=None):
        super().__init__(parent)
        global analyseActive

        self._on_close = on_close
        self._closing = False
        self._load_generation = 0
        analyseActive = True

        self.title(language_text("Analysis", "title"))
        self.resizable(True, True)
        self.minsize(900, 520)
        self.protocol("WM_DELETE_WINDOW", self.close)

        content = tk.Frame(self, padx=24, pady=20)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=0)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        file_frame = tk.Frame(content)
        file_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 24))
        file_frame.columnconfigure(0, weight=1)
        file_frame.rowconfigure(0, weight=1)

        self.file_list = tk.Listbox(
            file_frame,
            selectmode=tk.BROWSE,
            exportselection=False,
            width=25,
            font=font.Font(family="Consolas", size=9),
        )
        file_scrollbar = tk.Scrollbar(
            file_frame,
            orient=tk.VERTICAL,
            command=self.file_list.yview,
        )
        self.file_list.configure(yscrollcommand=file_scrollbar.set)
        self.file_list.grid(row=0, column=0, sticky="nsew")
        file_scrollbar.grid(row=0, column=1, sticky="ns")

        self.simhub_data_files = get_simhub_data_files()
        for file_path in self.simhub_data_files:
            self.file_list.insert(tk.END, file_path.stem)
        if self.simhub_data_files:
            self.file_list.selection_set(0)
            self.file_list.activate(0)

        detail_frame = tk.Frame(content)
        detail_frame.grid(row=0, column=1, sticky="nsew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(2, weight=1)

        axis_button_frame = tk.Frame(detail_frame)
        axis_button_frame.grid(row=0, column=0, sticky="w", pady=(0, 8))
        tk.Button(
            axis_button_frame,
            text=language_text("Analysis", "deselect_all_axes"),
            command=self._deselect_all_axes,
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            axis_button_frame,
            text=language_text("Analysis", "select_hub_axes"),
            command=self._select_hub_axes,
        ).pack(side=tk.LEFT)

        axis_frame = tk.Frame(detail_frame)
        axis_frame.grid(row=1, column=0, sticky="new")
        self.axis_selected = []
        for column, label_text in enumerate(AXIS_LABELS):
            axis_frame.columnconfigure(column, weight=1)
            tk.Label(
                axis_frame,
                text=label_text,
                font=font.Font(family="Segoe UI", size=9),
            ).grid(row=0, column=column, padx=6, sticky="s")
            selected = tk.BooleanVar(value=True)
            self.axis_selected.append(selected)
            tk.Checkbutton(
                axis_frame,
                variable=selected,
                command=self._update_chart_axes,
            ).grid(row=1, column=column, padx=6, sticky="n")

        self.chart = SignalChart(detail_frame)
        self.chart.grid(row=2, column=0, sticky="nsew", pady=(8, 12))

        signal_frame = tk.Frame(detail_frame)
        signal_frame.grid(row=3, column=0, sticky="ew")
        self.signal_selected = {
            SIGNAL_TARGET: tk.BooleanVar(value=True),
            SIGNAL_CALCULATED_ACTUAL: tk.BooleanVar(value=False),
            SIGNAL_ACTUAL: tk.BooleanVar(value=False),
            SIGNAL_GROUND_DEVIATION: tk.BooleanVar(value=False),
        }
        signal_labels = (
            (SIGNAL_TARGET, language_text("Analysis", "signal_target")),
            (
                SIGNAL_CALCULATED_ACTUAL,
                language_text("Analysis", "signal_calculated_actual"),
            ),
            (SIGNAL_ACTUAL, language_text("Analysis", "signal_actual")),
            (
                SIGNAL_GROUND_DEVIATION,
                language_text("Analysis", "signal_ground_deviation"),
            ),
        )
        for column, (signal_name, label_text) in enumerate(signal_labels):
            signal_frame.columnconfigure(column, weight=1)
            checkbutton = tk.Checkbutton(
                signal_frame,
                text=label_text,
                variable=self.signal_selected[signal_name],
                command=self._update_chart_signals,
            )
            checkbutton.grid(row=0, column=column, padx=12, sticky="n")
            if signal_name == SIGNAL_GROUND_DEVIATION:
                self.ground_deviation_checkbutton = checkbutton
        self._update_ground_deviation_availability()

        self.file_list.bind("<<ListboxSelect>>", self._file_selected)
        if self.simhub_data_files:
            self.after_idle(self._load_selected_file)
        self.after_idle(self._show_window)

    def _file_selected(self, _event=None):
        self._load_selected_file()

    def _load_selected_file(self):
        selection = self.file_list.curselection()
        if not selection:
            self.chart.set_message(language_text("Analysis", "no_file_selected"))
            return

        file_path = self.simhub_data_files[selection[0]]
        self._load_generation += 1
        generation = self._load_generation
        self.chart.set_message(language_text("Analysis", "loading_data"))
        threading.Thread(
            target=self._load_recording_worker,
            args=(file_path, generation),
            daemon=True,
        ).start()

    def _load_recording_worker(self, file_path, generation):
        try:
            samples = load_recording(file_path)
            error = None
        except Exception as load_error:
            samples = []
            error = str(load_error)

        if self._closing:
            return
        try:
            self.after(0, self._apply_recording, file_path, generation, samples, error)
        except tk.TclError:
            pass

    def _apply_recording(self, file_path, generation, samples, error):
        if self._closing or generation != self._load_generation:
            return
        if error is not None:
            logger.warning("Recording %s could not be loaded: %s", file_path.name, error)
            self.chart.set_message(language_text("Analysis", "file_load_error"))
            return
        self.chart.set_data(samples)

    def _update_chart_axes(self):
        self.chart.set_selected_axes(
            selected.get() for selected in self.axis_selected
        )
        self._update_ground_deviation_availability()

    def _deselect_all_axes(self):
        for selected in self.axis_selected:
            selected.set(False)
        self._update_chart_axes()

    def _select_hub_axes(self):
        for index, selected in enumerate(self.axis_selected):
            selected.set(index in HUB_AXIS_INDEXES)
        self._update_chart_axes()

    def _update_ground_deviation_availability(self):
        hub_axis_selected = any(
            self.axis_selected[index].get() for index in HUB_AXIS_INDEXES
        )
        if hub_axis_selected:
            self.ground_deviation_checkbutton.configure(state=tk.NORMAL)
            return
        self.signal_selected[SIGNAL_GROUND_DEVIATION].set(False)
        self.ground_deviation_checkbutton.configure(state=tk.DISABLED)
        self._update_chart_signals()

    def _update_chart_signals(self):
        self.chart.set_selected_signals(
            {
                signal_name: selected.get()
                for signal_name, selected in self.signal_selected.items()
            }
        )

    def _show_window(self):
        if self._closing or not self.winfo_exists():
            return

        self.update_idletasks()
        width = max(900, self.winfo_reqwidth())
        height = max(520, self.winfo_reqheight())
        parent_x = self.master.winfo_rootx()
        parent_y = self.master.winfo_rooty()
        parent_width = self.master.winfo_width()
        parent_height = self.master.winfo_height()
        x = parent_x + max(0, (parent_width - width) // 2)
        y = parent_y + max(0, (parent_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.deiconify()
        try:
            self.state("zoomed")
        except tk.TclError:
            self.geometry(
                f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0"
            )
        self.lift()
        self.focus_force()

    def close(self):
        global analyseActive

        if self._closing:
            return
        self._closing = True
        self._load_generation += 1
        analyseActive = False

        if self.winfo_exists():
            self.destroy()
        if self._on_close is not None:
            self._on_close()
