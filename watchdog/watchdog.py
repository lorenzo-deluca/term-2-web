"""
Serial Watchdog Service
=======================
Monitors the ser2net raw trace file for data activity.
If no new bytes are received for SILENCE_THRESHOLD_SECS, it assumes
the serial pipeline has stalled and restarts the ser2net container.

Exposes a lightweight HTTP health endpoint on port 8888.
"""

import os
import sys
import time
import logging
import threading
from datetime import datetime, timezone
from flask import Flask, jsonify
import docker

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TRACE_FILE            = os.environ.get('TRACE_FILE',            '/data/esp32_serial.trace')
WATCHDOG_LOG_FILE     = os.environ.get('WATCHDOG_LOG_FILE',     '/data/watchdog.log')
SER2NET_CONTAINER     = os.environ.get('SER2NET_CONTAINER',     'ser2web_ser2net')
SILENCE_THRESHOLD_SEC = int(os.environ.get('SILENCE_THRESHOLD_SEC', '60'))
CHECK_INTERVAL_SEC    = int(os.environ.get('CHECK_INTERVAL_SEC',     '10'))
HTTP_PORT             = int(os.environ.get('WATCHDOG_HTTP_PORT',     '8888'))

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [WATCHDOG] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger('watchdog')

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_state = {
    'last_data_ts':   None,   # datetime of last byte seen in trace (UTC)
    'restart_count':  0,      # total restarts triggered
    'last_restart_ts': None,  # datetime of last restart (UTC)
    'last_restart_reason': None,
    'status':         'starting',   # starting | ok | stale | restarting | error
    'ser2net_status': 'unknown',
}
_state_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Watchdog log helper
# ---------------------------------------------------------------------------
def _wlog(msg: str):
    """Append a timestamped line to the persistent watchdog log file."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(WATCHDOG_LOG_FILE, 'a') as f:
            f.write(f"[{ts}] {msg}\n")
            f.flush()
    except Exception as exc:
        log.warning("Could not write to watchdog log: %s", exc)


# ---------------------------------------------------------------------------
# Docker helper
# ---------------------------------------------------------------------------
def _get_docker_client():
    try:
        return docker.from_env()
    except Exception as exc:
        log.error("Cannot connect to Docker socket: %s", exc)
        return None


def _restart_ser2net(reason: str):
    """Restart the ser2net container and record the event."""
    log.warning("STALL DETECTED — restarting %s. Reason: %s", SER2NET_CONTAINER, reason)
    _wlog(f"STALL DETECTED — restarting {SER2NET_CONTAINER}. Reason: {reason}")

    client = _get_docker_client()
    if client is None:
        log.error("Docker client unavailable — cannot restart %s", SER2NET_CONTAINER)
        _wlog(f"ERROR: Docker client unavailable — manual restart required for {SER2NET_CONTAINER}")
        return False

    try:
        with _state_lock:
            _state['status'] = 'restarting'
        container = client.containers.get(SER2NET_CONTAINER)
        container.restart(timeout=15)

        with _state_lock:
            _state['restart_count'] += 1
            _state['last_restart_ts'] = datetime.now(timezone.utc)
            _state['last_restart_reason'] = reason
            _state['last_data_ts'] = None  # reset: wait for fresh data after restart
            _state['status'] = 'ok'

        msg = f"Successfully restarted {SER2NET_CONTAINER} (total restarts: {_state['restart_count']})"
        log.info(msg)
        _wlog(msg)
        return True

    except docker.errors.NotFound:
        msg = f"ERROR: Container {SER2NET_CONTAINER} not found — cannot restart"
        log.error(msg)
        _wlog(msg)
        with _state_lock:
            _state['status'] = 'error'
        return False
    except Exception as exc:
        msg = f"ERROR restarting {SER2NET_CONTAINER}: {exc}"
        log.error(msg)
        _wlog(msg)
        with _state_lock:
            _state['status'] = 'error'
        return False


def _get_ser2net_container_status() -> str:
    """Return ser2net container status string for monitoring."""
    client = _get_docker_client()
    if client is None:
        return 'docker_unavailable'
    try:
        container = client.containers.get(SER2NET_CONTAINER)
        return container.status  # 'running', 'exited', 'restarting', etc.
    except Exception:
        return 'not_found'


# ---------------------------------------------------------------------------
# Trace file monitor thread
# ---------------------------------------------------------------------------
def _monitor_loop():
    """
    Main watchdog loop:
     - Polls the trace file for size changes
     - If file grows → data is flowing, update last_data_ts
     - If file static for > SILENCE_THRESHOLD_SEC → restart ser2net
    """
    last_size = -1
    last_inode = -1

    log.info(
        "Watchdog started. trace=%s container=%s silence_threshold=%ds check_interval=%ds",
        TRACE_FILE, SER2NET_CONTAINER, SILENCE_THRESHOLD_SEC, CHECK_INTERVAL_SEC
    )
    _wlog(f"Watchdog started. trace={TRACE_FILE} container={SER2NET_CONTAINER} "
          f"silence_threshold={SILENCE_THRESHOLD_SEC}s")

    with _state_lock:
        _state['status'] = 'ok'

    while True:
        try:
            # --- Check ser2net container status ---
            ser2net_status = _get_ser2net_container_status()
            with _state_lock:
                _state['ser2net_status'] = ser2net_status

            if ser2net_status not in ('running',):
                # Container is not running — Docker will restart it (restart:always)
                # We just wait and update state
                log.warning("ser2net container status: %s — waiting for Docker to recover it", ser2net_status)
                with _state_lock:
                    _state['status'] = 'error'
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            # --- Probe trace file ---
            if not os.path.exists(TRACE_FILE):
                log.info("Trace file not found yet: %s — waiting", TRACE_FILE)
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            st = os.stat(TRACE_FILE)
            current_size  = st.st_size
            current_inode = st.st_ino

            # Detect file recreation (ser2net restart / truncation)
            if current_inode != last_inode:
                log.info("Trace file recreated (inode changed) — resetting counters")
                last_inode = current_inode
                last_size = current_size
                with _state_lock:
                    _state['last_data_ts'] = datetime.now(timezone.utc)
                    _state['status'] = 'ok'
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            if current_size > last_size:
                # Data is flowing
                with _state_lock:
                    _state['last_data_ts'] = datetime.now(timezone.utc)
                    _state['status'] = 'ok'
                last_size = current_size
            else:
                # No new data since last check — compute total silence duration
                with _state_lock:
                    last_ts = _state['last_data_ts']

                if last_ts is None:
                    # We have never seen data yet on this run — don't alarm
                    pass
                else:
                    silence_sec = (datetime.now(timezone.utc) - last_ts).total_seconds()
                    log.debug("Silence duration: %.0fs (threshold: %ds)", silence_sec, SILENCE_THRESHOLD_SEC)

                    if silence_sec >= SILENCE_THRESHOLD_SEC:
                        with _state_lock:
                            _state['status'] = 'stale'
                        _restart_ser2net(
                            reason=f"No serial data for {silence_sec:.0f}s (threshold={SILENCE_THRESHOLD_SEC}s)"
                        )
                        # After restart, reset trace size tracking
                        last_size = -1
                    else:
                        with _state_lock:
                            _state['status'] = 'ok'

        except Exception as exc:
            log.error("Monitor loop error: %s", exc)
            with _state_lock:
                _state['status'] = 'error'

        time.sleep(CHECK_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# HTTP health endpoint
# ---------------------------------------------------------------------------
http_app = Flask(__name__)


@http_app.route('/health')
def health():
    with _state_lock:
        last_ts   = _state['last_data_ts']
        last_rst  = _state['last_restart_ts']
        status    = _state['status']
        restarts  = _state['restart_count']
        reason    = _state['last_restart_reason']
        s2n_st    = _state['ser2net_status']

    silence_sec = None
    if last_ts is not None:
        silence_sec = round((datetime.now(timezone.utc) - last_ts).total_seconds(), 1)

    return jsonify({
        'status':               status,
        'ser2net_container':    s2n_st,
        'silence_seconds':      silence_sec,
        'silence_threshold':    SILENCE_THRESHOLD_SEC,
        'last_data_utc':        last_ts.isoformat() if last_ts else None,
        'restart_count':        restarts,
        'last_restart_utc':     last_rst.isoformat() if last_rst else None,
        'last_restart_reason':  reason,
    })


@http_app.route('/ping')
def ping():
    return 'pong', 200


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    # Start monitor thread
    t = threading.Thread(target=_monitor_loop, daemon=True, name='monitor')
    t.start()

    # Start HTTP server (blocking)
    log.info("HTTP health endpoint listening on port %d", HTTP_PORT)
    http_app.run(host='0.0.0.0', port=HTTP_PORT, use_reloader=False)
