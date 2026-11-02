import socket
import sys
from pathlib import Path

if __package__ in (None, ""):
    package_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(package_dir.parent))
    __package__ = package_dir.name

from .LIB.logging_config import get_logger
from .SimHub2SimRig import UDP_IP, UDP_PORT

logger = get_logger("shutdown")


def main():
    """Request a clean shutdown and wait until handle_end() has finished."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((UDP_IP, 0))
            sock.settimeout(5.0)
            sock.sendto(b"SHUTDOWN", (UDP_IP, UDP_PORT))

            while True:
                try:
                    data, _ = sock.recvfrom(4096)
                except TimeoutError as e:
                    raise RuntimeError(
                        "SimHub2SimRig did not accept the shutdown request."
                    ) from e

                response = data.decode("utf-8", errors="replace")
                if response == "SHUTDOWN_ACCEPTED":
                    logger.info("Shutdown accepted; waiting for handle_end")
                    sock.settimeout(None)
                elif response == "SHUTDOWN_COMPLETE":
                    logger.info("Shutdown complete")
                    return 0
                elif response.startswith("SHUTDOWN_ERROR"):
                    raise RuntimeError(response)
    except Exception as e:
        logger.error("Shutdown failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
