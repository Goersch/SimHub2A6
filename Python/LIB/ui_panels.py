"""Named UI panels that define ownership boundaries inside the main dialog."""

import tkinter as tk
from tkinter import font

from .language import text as language_text


class MotorPanel(tk.Frame):
    """Container for per-axis status controls."""

    def build_table(self, axis_labels, motor_count, colors):
        header_bg, header_fg, cell_bg, cell_fg = colors
        header_font = font.Font(family="Segoe UI", size=8, weight="bold")
        cell_font = font.Font(family="Segoe UI", size=8)
        for column, text in enumerate(
            (
                language_text("MainDialog", "table_axis"),
                language_text("MainDialog", "table_leveling_offset"),
                language_text("MainDialog", "table_load_rate"),
                language_text("MainDialog", "table_state"),
                language_text("MainDialog", "table_grease"),
            )
        ):
            tk.Label(self, text=text, font=header_font, bg=header_bg, fg=header_fg,
                     padx=12, pady=7, borderwidth=1, relief="solid").grid(
                row=0, column=column, sticky="nsew"
            )
        value_labels = []
        widths = (8, 12, 14, 14, 14)
        for axis in range(1, motor_count + 1):
            labels = []
            texts = (
                axis_labels.get(axis, str(axis)),
                language_text("Common", "placeholder"),
                language_text("Common", "placeholder"),
                language_text("Common", "placeholder"),
                language_text("Common", "status_ok"),
            )
            for column, (text, width) in enumerate(zip(texts, widths, strict=False)):
                label = tk.Label(self, text=text, font=cell_font, bg=cell_bg,
                                 fg=cell_fg, padx=12, pady=7, borderwidth=1,
                                 relief="solid", width=width)
                label.grid(row=axis, column=column, sticky="nsew")
                labels.append(label)
            value_labels.append(tuple(labels))
        for column in range(5):
            self.grid_columnconfigure(column, weight=1)
        return value_labels


class GreasePanel(tk.Frame):
    """Container for grease controls and runtime counters."""

    def build_table(self, rows, on_start, on_stop, on_reset, colors):
        header_bg, header_fg, cell_bg, cell_fg = colors
        header_font = font.Font(family="Segoe UI", size=8, weight="bold")
        cell_font = font.Font(family="Segoe UI", size=8)
        button_font = font.Font(family="Segoe UI", size=8)
        tk.Label(self, text=language_text("Panels", "grease_title"), font=header_font, bg=header_bg, fg=header_fg,
                 padx=12, pady=7, borderwidth=1, relief="solid").grid(
            row=0, column=0, columnspan=3, sticky="nsew"
        )
        for column, text in enumerate(
            (
                language_text("Panels", "grease_reset"),
                language_text("Panels", "grease_last"),
                language_text("Panels", "grease_playtime"),
            ),
            3,
        ):
            tk.Label(self, text=text, font=header_font, bg=header_bg, fg=header_fg,
                     padx=12, pady=7, borderwidth=1, relief="solid").grid(
                row=0, column=column, sticky="nsew"
            )
        buttons, info_labels = [], []
        for row_index, (axis_from, axis_to, label_text) in enumerate(rows, 1):
            axis_label = tk.Label(self, text=label_text, font=cell_font, bg=cell_bg,
                                  fg=cell_fg, padx=12, pady=7, borderwidth=1,
                                  relief="solid", width=8)
            start = tk.Button(self, text=language_text("Panels", "grease_start"), font=button_font, width=8,
                              command=lambda a=axis_from, b=axis_to: on_start(a, b))
            stop = tk.Button(self, text=language_text("Panels", "grease_stop"), font=button_font, width=8,
                             state=tk.DISABLED,
                             command=lambda a=axis_from, b=axis_to: on_stop(a, b))
            reset = tk.Button(self, text=language_text("Panels", "grease_reset"), font=button_font, width=8,
                              command=lambda a=axis_from, b=axis_to: on_reset(a, b))
            last = tk.Label(self, text=language_text("Common", "placeholder"), font=cell_font, bg=cell_bg, fg=cell_fg,
                            padx=12, pady=7, borderwidth=1, relief="solid", width=18)
            playtime = tk.Label(self, text=language_text("Common", "placeholder"), font=cell_font, bg=cell_bg, fg=cell_fg,
                                padx=12, pady=7, borderwidth=1, relief="solid", width=10)
            for column, widget in enumerate((axis_label, start, stop, reset, last, playtime)):
                widget.grid(row=row_index, column=column, sticky="nsew")
            buttons.append((axis_from, axis_to, start, stop))
            key = str(axis_from) if axis_from == axis_to else f"{axis_from}-{axis_to}"
            info_labels.append((key, last, playtime))
        for column in range(6):
            self.grid_columnconfigure(column, weight=1)
        return buttons, info_labels


class LevelingPanel(tk.Frame):
    """Container for leveling actions."""

    def build_controls(self, on_start, on_stop, on_save_load_values):
        button_font = font.Font(family="Segoe UI", size=8, weight="bold")
        self.grid_rowconfigure(0, weight=1, uniform="button_rows")
        self.grid_rowconfigure(1, weight=1, uniform="button_rows")
        self.grid_rowconfigure(2, weight=1, uniform="button_rows")
        start = tk.Button(self, text=language_text("Panels", "leveling_start"), font=button_font,
                          command=on_start, width=18)
        stop = tk.Button(self, text=language_text("Panels", "leveling_stop"), font=button_font,
                         command=on_stop, width=18, state=tk.DISABLED)
        save_load_values = tk.Button(
            self,
            text=language_text("Panels", "save_load_values"),
            font=button_font,
            command=on_save_load_values,
            width=18,
        )
        start.grid(row=0, column=0, padx=(0, 6), sticky="e")
        stop.grid(row=1, column=0, padx=(0, 6), pady=(6, 0), sticky="e")
        save_load_values.grid(
            row=2, column=0, padx=(0, 6), pady=(6, 0), sticky="e"
        )
        return start, stop, save_load_values


class LightPanel(tk.Frame):
    """Container for room-light, analysis and motion-mode actions."""

    def build_controls(
        self,
        on_light,
        on_analyse,
        on_maintenance,
        motion_mode,
        on_mode_change,
    ):
        button_font = font.Font(family="Segoe UI", size=8, weight="bold")
        self.grid_rowconfigure(0, weight=1, uniform="button_rows")
        self.grid_rowconfigure(1, weight=1, uniform="button_rows")
        light_on = tk.Button(self, text=language_text("Panels", "light_on"), font=button_font,
                             command=lambda: on_light(True), width=12)
        light_off = tk.Button(self, text=language_text("Panels", "light_off"), font=button_font,
                              command=lambda: on_light(False), width=12)
        analyse = tk.Button(self, text=language_text("Panels", "analysis"), font=button_font,
                            command=on_analyse, width=12)
        maintenance = tk.Button(self, text=language_text("Panels", "maintenance"), font=button_font,
                                command=on_maintenance, width=12)
        mode_frame = tk.LabelFrame(self, text=language_text("Panels", "mode"), font=button_font,
                                   padx=6, pady=2)
        simhub = tk.Radiobutton(mode_frame, text=language_text("Panels", "mode_active"), variable=motion_mode,
                                value="simhub", command=on_mode_change)
        actual_positions = tk.Radiobutton(
            mode_frame,
            text=language_text("Panels", "mode_actual_positions"),
            variable=motion_mode,
            value="actual_positions",
            command=on_mode_change,
        )
        center = tk.Radiobutton(mode_frame, text=language_text("Panels", "mode_inactive"), variable=motion_mode,
                                value="center", command=on_mode_change)
        simhub.grid(row=0, column=0, sticky="w")
        actual_positions.grid(row=1, column=0, sticky="w")
        center.grid(row=2, column=0, sticky="w")
        light_on.grid(row=0, column=0, padx=(0, 6), sticky="e")
        light_off.grid(row=1, column=0, padx=(0, 6), pady=(6, 0), sticky="e")
        analyse.grid(row=0, column=1, sticky="e")
        maintenance.grid(row=1, column=1, pady=(6, 0), sticky="e")
        mode_frame.grid(row=0, column=2, rowspan=2, padx=(12, 0), sticky="nsw")
        return analyse, maintenance, simhub, actual_positions, center


class ChartPanel(tk.Frame):
    """Container for timing charts."""
