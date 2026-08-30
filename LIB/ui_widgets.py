"""Reusable Tk widgets used by the debug dialog."""

import time
import tkinter as tk
from datetime import datetime
from tkinter import font

from .config import SIMHUB_CONFIG, UI_CONFIG

VALUE_MIN = SIMHUB_CONFIG.position_min
VALUE_MAX = SIMHUB_CONFIG.position_max
TRIGGER_CHART_WINDOW_S = UI_CONFIG.trigger_chart_window_s
TRIGGER_CHART_MAX_MS = UI_CONFIG.trigger_chart_max_ms
TABLE_BORDER = "#AAB7C4"
DISCONNECTED_BG = UI_CONFIG.disconnected_background
RESIZE_REDRAW_DELAY_MS = 100


class BarGraph(tk.Canvas):
    def __init__(
        self, parent, orientation="horizontal", width=320, height=80,
        label=None, bg=None, **kwargs,
    ):
        super().__init__(
            parent, width=width, height=height, bg=bg or DISCONNECTED_BG,
            highlightthickness=0, **kwargs,
        )
        self.orientation = orientation
        self.value = 0
        self.label = label
        self.base_font = font.Font(family="Segoe UI", size=11, weight="bold")
        self.draw()

    def set_value(self, value):
        self.value = max(VALUE_MIN, min(VALUE_MAX, value))
        self.draw()

    def set_bg(self, bg):
        self.configure(bg=bg)
        self.draw()

    def draw(self):
        if not self.winfo_exists():
            return
        try:
            self.delete("all")
        except tk.TclError:
            return
        width = max(int(self.winfo_reqwidth()), int(self["width"]))
        height = max(int(self.winfo_reqheight()), int(self["height"]))
        margin = 12
        if self.orientation == "horizontal":
            x0, y0, x1, y1 = margin, height // 3, width - margin, height * 2 // 3
        else:
            x0, y0, x1, y1 = width // 3, margin, width * 2 // 3, height - margin
        self.create_rectangle(x0, y0, x1, y1, fill="#DCE6F1", outline="#4A6D92", width=2)
        fraction = (self.value - VALUE_MIN) / (VALUE_MAX - VALUE_MIN)
        if self.orientation == "horizontal":
            zero = x0 + (x1 - x0) // 2
            marker = x0 + int(fraction * (x1 - x0))
            self.create_line(zero, y0 + 2, zero, y1 - 2, fill="#4A6D92")
            self.create_line(marker, y0, marker, y1, fill="#000000", width=4)
        else:
            zero = y0 + (y1 - y0) // 2
            marker = y1 - int(fraction * (y1 - y0))
            self.create_line(x0 + 2, zero, x1 - 2, zero, fill="#4A6D92")
            self.create_line(x0, marker, x1, marker, fill="#000000", width=4)


class TriggerIntervalChart(tk.Canvas):
    def __init__(self, parent, width=520, height=170):
        super().__init__(parent, width=width, height=height, bg="#FFFFFF",
                         highlightthickness=1, highlightbackground=TABLE_BORDER)
        self.samples = []
        self.now = time.time()
        self._redraw_after_id = None
        self._last_configure_size = None
        self.bind("<Configure>", self._schedule_redraw)

    def set_samples(self, samples, now):
        self.samples = samples
        self.now = now
        self.draw()

    def _schedule_redraw(self, _event=None):
        if _event is not None:
            configure_size = (_event.width, _event.height)
            if configure_size == self._last_configure_size:
                return
            self._last_configure_size = configure_size
        if self._redraw_after_id is not None:
            self.after_cancel(self._redraw_after_id)
        # ``after_idle`` still runs repeatedly while Windows is processing an
        # interactive window resize.  Wait until the stream of Configure
        # events has settled so dragging the window border stays responsive.
        self._redraw_after_id = self.after(
            RESIZE_REDRAW_DELAY_MS,
            self._run_scheduled_redraw,
        )

    def _run_scheduled_redraw(self):
        self._redraw_after_id = None
        self.draw()

    def draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 160 or height < 100:
            return
        left, right, top, bottom = 50, width - 12, 12, height - 38
        plot_width, plot_height = max(1, right - left), max(1, bottom - top)
        for value in (0, 50, 100, 150, 200):
            y = bottom - value / TRIGGER_CHART_MAX_MS * plot_height
            self.create_line(left, y, right, y, fill="#D9E0E7")
            self.create_text(left - 6, y, text=str(value), anchor="e", fill="#333333")
        self.create_line(left, top, left, bottom, fill="#333333")
        self.create_line(left, bottom, right, bottom, fill="#333333")
        start_time = self.now - TRIGGER_CHART_WINDOW_S
        for tick in range(5):
            fraction = tick / 4
            x = left + fraction * plot_width
            timestamp = start_time + fraction * TRIGGER_CHART_WINDOW_S
            self.create_text(x, bottom + 7, text=datetime.fromtimestamp(timestamp).strftime("%H:%M:%S"), anchor="n")
        coordinates = []
        for timestamp, interval_ms in self.samples:
            if start_time <= timestamp <= self.now:
                coordinates.extend((
                    left + (timestamp - start_time) / TRIGGER_CHART_WINDOW_S * plot_width,
                    bottom - max(0.0, min(TRIGGER_CHART_MAX_MS, interval_ms)) / TRIGGER_CHART_MAX_MS * plot_height,
                ))
        if len(coordinates) >= 4:
            self.create_line(*coordinates, fill="#0078D4", width=1.5)
