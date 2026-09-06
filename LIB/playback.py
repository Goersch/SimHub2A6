"""Playback timing and driver dispatch for recorded target positions."""
import threading
import time

from .. import SimHubCommands as shcmd
from .config import RIG_CONFIG


class PlaybackController:
    """Runs a recording against the driver without blocking Tkinter."""
    def __init__(self, on_position=None, on_finished=None):
        self.on_position = on_position
        self.on_finished = on_finished
        self.state = "stopped"
        self.samples = []
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self._pause_started = None
        self._paused_for = 0.0

    def set_samples(self, samples):
        self.samples = list(samples or [])

    def play(self):
        if self.state == "paused":
            if self._pause_started is not None:
                self._paused_for += time.perf_counter() - self._pause_started
            self._pause_started = None
            self.pause_event.clear(); self.state = "playing"
            return
        if not self.samples or self.state == "playing": return
        self.stop_event.clear(); self.pause_event.clear(); self._paused_for = 0.0
        self.state = "playing"
        threading.Thread(target=self._run, daemon=True).start()

    def pause(self):
        if self.state != "playing": return
        self._pause_started = time.perf_counter(); self.pause_event.set(); self.state = "paused"

    def stop(self):
        self.stop_event.set(); self.pause_event.clear(); self.state = "stopped"

    def _run(self):
        try:
            shcmd.arm_simhub_positions()
            start = self.samples[0][0]; wall_start = time.perf_counter()
            for timestamp, signals in self.samples:
                if self.stop_event.is_set(): return
                while self.pause_event.is_set() and not self.stop_event.is_set(): time.sleep(.05)
                deadline = wall_start + timestamp - start + self._paused_for
                if self.stop_event.wait(max(0.0, deadline - time.perf_counter())): return
                for axis, mm in enumerate(signals["target"], 1):
                    cfg = RIG_CONFIG.axis(axis)
                    raw = round((mm - cfg.zero_offset_mm - shcmd.levelingOffset[axis-1] + cfg.stroke_mm/2) / cfg.stroke_mm * shcmd.maxPos)
                    shcmd.handle_pos_2(axis, max(0, min(shcmd.maxPos, raw)), trigger=False)
                shcmd.motion_controller.planner_trigger(0, log_hex=False, check_crc=True)
                if self.on_position: self.on_position(timestamp)
        finally:
            if not self.stop_event.is_set():
                self.state = "stopped"
                if self.on_finished: self.on_finished()
