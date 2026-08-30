import signal
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path

if __package__ in (None, ""):
    package_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(package_dir.parent))
    __package__ = package_dir.name

from . import Analyse
from . import SimHubCommands as shcmd
from .Dialog import Dialog
from .LIB.a6_motion_controller import motion_controller
from .LIB.a6_simu import a6_simulator
from .LIB.config import SIMHUB_CONFIG
from .LIB.logging_config import configure_logging, get_logger

logger = get_logger("simhub.app")

UDP_IP = SIMHUB_CONFIG.udp_ip
UDP_PORT = SIMHUB_CONFIG.udp_port
SIMHUB_DISCONNECT_TIMEOUT_S = SIMHUB_CONFIG.disconnect_timeout_s
SIMHUB_POSITION_COUNT = SIMHUB_CONFIG.position_count
SIMHUB_POSITION_MIN = SIMHUB_CONFIG.position_min
SIMHUB_POSITION_MAX = SIMHUB_CONFIG.position_max
SimHubConnected = False
SimHubGameRunning = False

_CENTER_POSITION = (SIMHUB_POSITION_MIN + SIMHUB_POSITION_MAX) // 2
latestValues = [_CENTER_POSITION] * SIMHUB_POSITION_COUNT
latestLock = threading.Lock()
stopEvent = threading.Event()
shutdownRequested = threading.Event()
shutdownReplyAddresses = set()
shutdownReplyLock = threading.Lock()
triggerIntervalSamples = deque(maxlen=10000)
triggerIntervalLock = threading.Lock()

LOG_FILE = Path(__file__).with_name("LOG") / "simhub2a6.log"
def install_file_logging():
    configure_logging(LOG_FILE)
    logger.info("Logging to %s", LOG_FILE)

def delete_expired_simhub_data():
    a6_simulator.delete_expired_recordings()

def start_simhub_recording():
    a6_simulator.set_game_running(True)

def stop_simhub_recording():
    a6_simulator.set_game_running(False)

def set_values(a0=65535, a1=65535, a2=65535, a3=65535, a4=65535, a5=65535, a6=65535):
    with latestLock:
        latestValues[0] = a0
        latestValues[1] = a1
        latestValues[2] = a2
        latestValues[3] = a3
        latestValues[4] = a4
        latestValues[5] = a5
        latestValues[6] = a6

def sender_thread():

    cycle_s = SIMHUB_CONFIG.sender_cycle_s
    next_time = time.perf_counter()
    previous_cycle_time = None

    while not stopEvent.is_set():
        with latestLock:
            values = latestValues.copy()

        if not Analyse.analyseActive:
            try:
                position_updated = False
                position_updated |= shcmd.handle_pos_2(1,values[0], trigger=False)
                position_updated |= shcmd.handle_pos_2(2,values[1], trigger=False)
                position_updated |= shcmd.handle_pos_2(3,values[2], trigger=False)
                position_updated |= shcmd.handle_pos_2(4,values[3], trigger=False)
                position_updated |= shcmd.handle_pos_2(5,values[4], trigger=False)
                position_updated |= shcmd.handle_pos_2(6,values[5], trigger=False)
                position_updated |= shcmd.handle_pos_2(7,values[6], trigger=False)

                # Keep measuring every sender cycle so the chart remains
                # useful while the rig is stationary.
                cycle_time = time.perf_counter()
                if previous_cycle_time is not None:
                    interval_ms = (cycle_time - previous_cycle_time) * 1000.0
                    with triggerIntervalLock:
                        triggerIntervalSamples.append((time.time(), interval_ms))
                previous_cycle_time = cycle_time

                # A trigger is useful only after at least one new target was
                # written.  This removes 200 idle broadcast frames per second.
                if position_updated:
                    motion_controller.planner_trigger(0)
                    a6_simulator.planner_trigger(0)
            except Exception as e:
                logger.warning("Sender thread communication error: %s", e)
                time.sleep(0.1)

        next_time += cycle_s
        rest = next_time - time.perf_counter()
        if rest > 0:
            time.sleep(rest)
        else:
            next_time = time.perf_counter()

def send_shutdown_response(message: str):
    with shutdownReplyLock:
        reply_addresses = tuple(shutdownReplyAddresses)

    if not reply_addresses:
        return

    data = message.encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reply_socket:
        for reply_address in reply_addresses:
            try:
                reply_socket.sendto(data, reply_address)
            except OSError as e:
                logger.warning("Could not send shutdown response to %s: %s", reply_address, e)

def _parse_positions(payload: str):
    values = payload.split()
    if len(values) != SIMHUB_POSITION_COUNT:
        raise ValueError(
            f"expected {SIMHUB_POSITION_COUNT} positions, got {len(values)}"
        )
    try:
        positions = tuple(int(value) for value in values)
    except ValueError as exc:
        raise ValueError("all positions must be integers") from exc
    invalid = [
        (axis, value)
        for axis, value in enumerate(positions, start=1)
        if not SIMHUB_POSITION_MIN <= value <= SIMHUB_POSITION_MAX
    ]
    if invalid:
        invalid_details = ", ".join(
            f"axis {axis}, target position {value}" for axis, value in invalid
        )
        raise ValueError(
            f"positions must be between {SIMHUB_POSITION_MIN} "
            f"and {SIMHUB_POSITION_MAX}; invalid: {invalid_details}"
        )
    return positions


def parse_and_dispatch(message: str, reply_address=None):
    global SimHubGameRunning
    if Analyse.analyseActive:
        return

    # Separate multiple commands in the packet with ';'
    parts = [p.strip() for p in message.replace("\r", "").split(";") if p.strip()]
    for part in parts:
        if not part:
            continue

        # Use the first token as the command and the rest as arguments
        tokens = part.split(maxsplit=1)
        cmd = tokens[0].upper()

        if cmd == "POSITIONS":
            if len(tokens) != 2:
                logger.warning("Invalid POSITIONS command: missing payload")
                continue
            try:
                positions = _parse_positions(tokens[1])
            except ValueError as exc:
                logger.warning("Invalid POSITIONS command: %s", exc)
                continue
            if not SimHubGameRunning:
                logger.info("SimHub telemetric stream started without explicit START command")
                SimHubGameRunning = True
                start_simhub_recording()
            set_values(*positions)
        elif cmd == "END":
            if SimHubGameRunning:
                logger.info("SimHub game ended")
            SimHubGameRunning = False
            stop_simhub_recording()
        elif cmd == "START":
            if not SimHubGameRunning:
                logger.info("SimHub game started")
            SimHubGameRunning = True
            start_simhub_recording()
        elif cmd == "SHUTDOWN":
            logger.info("Shutdown requested")
            if reply_address is not None:
                with shutdownReplyLock:
                    shutdownReplyAddresses.add(reply_address)
                send_shutdown_response("SHUTDOWN_ACCEPTED")
            shutdownRequested.set()
        else:
            logger.warning("Unknown command: %r", cmd)


def udp_listener(sock):
    global SimHubConnected, SimHubGameRunning
    last_packet_time = None
    while not stopEvent.is_set():
        try:
            data, addr = sock.recvfrom(4096)
            last_packet_time = time.monotonic()
            if not SimHubConnected:
                logger.info("SimHub connected")
                SimHubConnected = True
            text = data.decode("utf-8", errors="ignore").strip()
            if not text:
                continue
            parse_and_dispatch(text, addr)
        except TimeoutError:
            if (
                SimHubConnected
                and last_packet_time is not None
                and time.monotonic() - last_packet_time >= SIMHUB_DISCONNECT_TIMEOUT_S
            ):
                SimHubConnected = False
                logger.info("SimHub disconnected")
                SimHubGameRunning = False
                stop_simhub_recording()
            continue
        except OSError:
            break
        except Exception:
            logger.exception("UDP listener error")
            continue

    if SimHubConnected:
        SimHubConnected = False
        logger.info("SimHub disconnected")
    SimHubGameRunning = False
    stop_simhub_recording()


def main():

    stopEvent.clear()
    shutdownRequested.clear()
    with shutdownReplyLock:
        shutdownReplyAddresses.clear()
    with triggerIntervalLock:
        triggerIntervalSamples.clear()
    install_file_logging()
    delete_expired_simhub_data()
    logger.info("Application starting")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((UDP_IP, UDP_PORT))
        sock.settimeout(1.0)
    except Exception:
        sock.close()
        logger.exception(
            "Failed to reserve UDP listener %s:%s; another instance may still be shutting down",
            UDP_IP,
            UDP_PORT,
        )
        return

    try:
        shcmd.handle_init()
    except Exception:
        logger.exception("Failed to initialize A6; check remote switches")

    a6_simulator.start()

    tx_thread = threading.Thread(target=sender_thread, daemon=True)
    tx_thread.start()

    listener_thread = threading.Thread(target=udp_listener, args=(sock,), daemon=True)
    listener_thread.start()
    logger.info("UDP listener started on %s:%s", UDP_IP, UDP_PORT)

    closing = False
    root = None

    def on_close():
        nonlocal closing
        if closing:
            return

        closing = True
        stopEvent.set()
        if root is not None:
            root.close()

    def on_sigint(signum, frame):
        logger.info("Terminating program (CTRL+C)")
        on_close()

    try:
        root = Dialog()
        root.resizable(True, True)
        root.protocol("WM_DELETE_WINDOW", on_close)
        signal.signal(signal.SIGINT, on_sigint)

        def check_shutdown_request():
            if shutdownRequested.is_set():
                on_close()
                return
            root.after(100, check_shutdown_request)

        root.after(100, check_shutdown_request)
        root.mainloop()
    except KeyboardInterrupt:
        logger.info("Terminating program (CTRL+C)")
        on_close()
    finally:
        stopEvent.set()
        if root is not None:
            try:
                root.close()
            except Exception:
                logger.exception("Failed to close dialog during shutdown")
        stop_simhub_recording()
        a6_simulator.stop()
        tx_thread.join()
        listener_thread.join(timeout=1.5)
        try:
            try:
                shcmd.handle_end()
            except Exception as e:
                send_shutdown_response(f"SHUTDOWN_ERROR {e}")
                raise
            else:
                send_shutdown_response("SHUTDOWN_COMPLETE")
        finally:
            try:
                sock.close()
            except Exception:
                pass
            logger.info("Socket closed")

if __name__ == "__main__":
    main()
