"""Application-wide logging with optional file and GUI sinks."""

import logging
import sys
from collections.abc import Callable
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%H:%M:%S"
_configured = False
_gui_handler = None


class GuiLogHandler(logging.Handler):
    def __init__(self, sink: Callable[[str], None]) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink(self.format(record))
        except Exception:
            self.handleError(record)


def configure_logging(log_file: Path | None = None, level=logging.INFO) -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(level)
    if not _configured:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(stream)
        _configured = True
    if log_file is not None:
        resolved = Path(log_file).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if not any(
            isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename).resolve() == resolved
            for handler in root.handlers
        ):
            file_handler = logging.FileHandler(resolved, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
            root.addHandler(file_handler)


def set_gui_log_sink(sink: Callable[[str], None] | None) -> None:
    global _gui_handler
    root = logging.getLogger()
    if _gui_handler is not None:
        root.removeHandler(_gui_handler)
        _gui_handler = None
    if sink is not None:
        _gui_handler = GuiLogHandler(sink)
        _gui_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(_gui_handler)


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
