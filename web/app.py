import os
import glob
import threading
import time
from datetime import datetime, timezone
from flask import Flask, request, render_template, jsonify
from ansi2html import Ansi2HTMLConverter
import docker

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG_FILE = '/data/ser2net.yaml'
LOG_DIR = '/data'
TRACE_FILE = os.path.join(LOG_DIR, 'esp32_serial.trace')
TRACE_MAX_SIZE = 50 * 1024 * 1024  # Truncate raw trace at 50 MB

# Silence alarm: log a WARNING if no data seen for this many seconds
TRACE_SILENCE_WARN_SEC = 60

docker_client = docker.from_env()
conv = Ansi2HTMLConverter(dark_bg=True)

# Create default ser2net config if missing
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'w') as f:
        f.write("connection: &con1\n  accepter: tcp,6666\n  enable: off\n")


# ---------------------------------------------------------------------------
# Shared state: track last-data timestamp for health monitoring
# ---------------------------------------------------------------------------
_data_state_lock = threading.Lock()
_data_state = {
    'last_data_ts': None,   # datetime (UTC) of last byte written to log
    'total_lines':  0,      # total lines processed since startup
}


def _update_last_data():
    with _data_state_lock:
        _data_state['last_data_ts'] = datetime.now(timezone.utc)
        _data_state['total_lines'] += 1


# ---------------------------------------------------------------------------
# Background trace-file watcher
# Reads the raw ser2net trace file, timestamps each line, writes daily logs.
# This approach does NOT connect to the ser2net TCP port, so ttyd is free to
# use the connection exclusively without conflicts.
# ---------------------------------------------------------------------------
_stop = threading.Event()
_silence_warned = False  # prevents log spam during a stall


def _today_log_path():
    return os.path.join(LOG_DIR, f"esp32_serial_{datetime.now():%Y-%m-%d}.log")


def _trace_watcher():
    global _silence_warned

    last_inode = None
    last_pos = 0
    buf = ''
    current_day = None
    fh = None
    last_activity_ts = None  # wall-clock time (monotonic-safe via time.monotonic)
    last_activity_mono = None

    while not _stop.is_set():
        try:
            if not os.path.exists(TRACE_FILE):
                _stop.wait(2)
                continue

            st = os.stat(TRACE_FILE)

            # Detect file recreation or truncation (ser2net restart)
            if st.st_ino != last_inode or st.st_size < last_pos:
                last_inode = st.st_ino
                last_pos = 0
                buf = ''
                last_activity_mono = None
                _silence_warned = False
                app.logger.info("Trace file recreated/reset — resetting watcher state")

            # Prevent unbounded trace growth
            if st.st_size > TRACE_MAX_SIZE:
                try:
                    open(TRACE_FILE, 'w').close()
                except Exception:
                    pass
                last_pos = 0
                buf = ''
                continue

            # Nothing new to read
            if st.st_size <= last_pos:
                # Check for silence alarm
                if last_activity_mono is not None:
                    silence_sec = time.monotonic() - last_activity_mono
                    if silence_sec >= TRACE_SILENCE_WARN_SEC and not _silence_warned:
                        app.logger.warning(
                            "SERIAL SILENCE: No data received for %.0fs — watchdog should trigger restart soon",
                            silence_sec
                        )
                        _silence_warned = True
                _stop.wait(0.3)
                continue

            # Read new data
            with open(TRACE_FILE, 'rb') as tf:
                tf.seek(last_pos)
                raw = tf.read()
                last_pos = tf.tell()

            # Data is flowing — reset silence state
            last_activity_mono = time.monotonic()
            _silence_warned = False

            text = raw.decode('utf-8', errors='replace')
            buf += text
            buf = buf.replace('\r\n', '\n').replace('\r', '\n')

            # Strip control chars (keep ESC for ANSI colours, keep \n and \t)
            buf = ''.join(
                c for c in buf
                if c == '\x1b' or c == '\n' or c == '\t' or ord(c) >= 32
            )

            while '\n' in buf:
                line, buf = buf.split('\n', 1)
                if not line.strip():
                    continue

                now = datetime.now()
                day = f"{now:%Y-%m-%d}"

                # Rotate file handle on day change
                if day != current_day:
                    if fh:
                        fh.close()
                    fh = open(os.path.join(LOG_DIR, f"esp32_serial_{day}.log"), 'a')
                    current_day = day

                fh.write(f"[{now:%Y-%m-%d %H:%M:%S}] {line}\n")
                fh.flush()

                # Update shared health state
                _update_last_data()

            _stop.wait(0.3)

        except Exception as exc:
            app.logger.error("Trace watcher error: %s", exc)
            if fh:
                try:
                    fh.close()
                except Exception:
                    pass
                fh = None
                current_day = None
            _stop.wait(3)


threading.Thread(target=_trace_watcher, daemon=True, name='trace-watcher').start()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_available_ports():
    """Scan /dev for serial ports."""
    patterns = [
        '/dev/ttyUSB*', '/dev/ttyACM*', '/dev/ttyAMA*', '/dev/ttyS*',
        '/dev/tty.usbserial*', '/dev/tty.usbmodem*',
        '/dev/cu.usbserial*', '/dev/cu.usbmodem*',
    ]
    ports = []
    for p in patterns:
        ports.extend(sorted(glob.glob(p)))
    return ports


def get_current_port():
    if not os.path.exists(CONFIG_FILE):
        return 'No configuration'
    with open(CONFIG_FILE) as f:
        for line in f:
            if 'serialdev,' in line:
                return line.split('serialdev,')[1].split(',')[0].strip()
    return 'Not configured'


def get_service_status(name):
    try:
        return docker_client.containers.get(name).status == 'running'
    except Exception:
        return False


def read_log(path, last_n=None, reverse=False):
    """Read a text log file, optionally tail and/or reverse."""
    try:
        with open(path, 'r', errors='replace') as f:
            lines = f.readlines()
        if last_n:
            lines = lines[-last_n:]
        if reverse:
            lines.reverse()
        return ''.join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _get_watchdog_status():
    """Fetch watchdog health from the watchdog container HTTP endpoint."""
    try:
        import urllib.request
        import json
        with urllib.request.urlopen('http://ser2web_watchdog:8888/health', timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/health')
def api_health():
    """Health endpoint used by Docker healthcheck and external monitors."""
    with _data_state_lock:
        last_ts = _data_state['last_data_ts']
        total   = _data_state['total_lines']

    silence_sec = None
    if last_ts is not None:
        silence_sec = round((datetime.now(timezone.utc) - last_ts).total_seconds(), 1)

    status = 'ok'
    if silence_sec is not None and silence_sec > TRACE_SILENCE_WARN_SEC:
        status = 'stale'

    return jsonify({
        'status':              status,
        'last_data_utc':       last_ts.isoformat() if last_ts else None,
        'silence_seconds':     silence_sec,
        'silence_threshold':   TRACE_SILENCE_WARN_SEC,
        'total_lines_logged':  total,
    })


@app.route('/api/status')
def api_status():
    with _data_state_lock:
        last_ts = _data_state['last_data_ts']

    silence_sec = None
    if last_ts is not None:
        silence_sec = round((datetime.now(timezone.utc) - last_ts).total_seconds(), 1)

    watchdog = _get_watchdog_status()

    return jsonify({
        'current_port':      get_current_port(),
        'available_ports':   get_available_ports(),
        'ser2net_active':    get_service_status('ser2web_ser2net'),
        'ttyd_active':       get_service_status('ser2web_ttyd'),
        'last_data_utc':     last_ts.isoformat() if last_ts else None,
        'silence_seconds':   silence_sec,
        'watchdog':          watchdog,
    })


@app.route('/api/apply', methods=['POST'])
def api_apply():
    port = request.json.get('port')
    with open(CONFIG_FILE, 'w') as f:
        f.write("connection: &con1\n")
        f.write("  accepter: tcp,6666\n")
        f.write("  enable: on\n")
        f.write("  options:\n")
        f.write("    kickolduser: true\n")
        f.write(f"    trace-read: {TRACE_FILE}\n")
        f.write(f"  connector: serialdev,{port},115200N81,local\n")

    try:
        docker_client.containers.get('ser2web_ser2net').restart(timeout=10)
        docker_client.containers.get('ser2web_ttyd').restart(timeout=10)
        return jsonify({'success': True})
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/logs/live')
def api_logs_live():
    log_path = _today_log_path()
    if not os.path.exists(log_path):
        return jsonify({'html': '<span style="color:#475569">Waiting for serial data…</span>'})
    text = read_log(log_path, last_n=200, reverse=True)
    return jsonify({'html': conv.convert(text, full=False)})


@app.route('/api/logs/archives')
def api_logs_archives():
    files = []
    for name in os.listdir(LOG_DIR):
        if name.startswith('esp32_serial_') and name.endswith('.log'):
            fp = os.path.join(LOG_DIR, name)
            sz = os.path.getsize(fp)
            if sz > 0:
                files.append({'name': name, 'size': sz})
    files.sort(key=lambda x: x['name'], reverse=True)
    return jsonify({'archives': files})


@app.route('/api/logs/read/<filename>')
def api_logs_read(filename):
    if '/' in filename or '..' in filename:
        return jsonify({'html': 'Invalid filename'}), 400
    fp = os.path.join(LOG_DIR, filename)
    if not os.path.exists(fp):
        return jsonify({'html': 'File not found'}), 404
    text = read_log(fp, last_n=2000, reverse=True)
    return jsonify({'html': conv.convert(text, full=False)})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)