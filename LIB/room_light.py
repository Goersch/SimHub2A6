"""Asynchronous room-light service."""

import subprocess
import threading

from .config import ROOM_LIGHT_CONFIG
from .logging_config import get_logger

logger = get_logger("room-light")
ROOM_LIGHT_URI = ROOM_LIGHT_CONFIG.uri


def set_room_light(enabled: bool) -> None:
    def worker():
        try:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command",
                    f"Invoke-WebRequest -UseBasicParsing -Uri '{ROOM_LIGHT_URI.format(status=int(enabled))}'",
                ],
                check=False,
                timeout=ROOM_LIGHT_CONFIG.request_timeout_s,
            )
        except Exception:
            logger.exception("Room light request failed")

    threading.Thread(target=worker, name="RoomLight", daemon=True).start()
