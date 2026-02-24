import os
import glob
import socket
import threading
import time
from datetime import datetime
from flask import Flask, request, render_template, jsonify
from ansi2html import Ansi2HTMLConverter
import docker

app = Flask(__name__)
CONFIG_FILE = '/data/ser2net.yaml'
LOG_DIR = '/data'
LOG_FILE = f'{LOG_DIR}/esp32_serial.log'
SER2NET_HOST = 'ser2web_ser2net'
SER2NET_PORT = 6666
docker_client = docker.from_env()
conv = Ansi2HTMLConverter(dark_bg=True)

if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'w') as f:
        f.write("connection: &con1\n  accepter: tcp,6666\n  enable: off\n")
if not os.path.exists(LOG_FILE):
    open(LOG_FILE, 'a').close()


# ---------------------------------------------------------------------------
# Background serial logger — connects to ser2net TCP and timestamps each line
# ---------------------------------------------------------------------------
_logger_stop = threading.Event()


def serial_logger():
    """Background thread: connects to ser2net, timestamps each line, writes to log."""
    while not _logger_stop.is_set():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((SER2NET_HOST, SER2NET_PORT))
            app.logger.info(f"Serial logger connected to {SER2NET_HOST}:{SER2NET_PORT}")

            buf = ''
            with open(LOG_FILE, 'a') as log:
                while not _logger_stop.is_set():
                    try:
                        data = sock.recv(4096)
                        if not data:
                            break  # Connection closed
                        text = data.decode('utf-8', errors='replace')
                        buf += text

                        # Normalize line endings and split into lines
                        buf = buf.replace('\r\n', '\n').replace('\r', '\n')
                        while '\n' in buf:
                            line, buf = buf.split('\n', 1)
                            ts = datetime.now().strftime('[%Y-%m-%d %H:%M:%S] ')
                            log.write(ts + line + '\n')
                            log.flush()
                    except socket.timeout:
                        # Flush partial buffer after timeout (incomplete line)
                        if buf.strip():
                            ts = datetime.now().strftime('[%Y-%m-%d %H:%M:%S] ')
                            log.write(ts + buf + '\n')
                            log.flush()
                            buf = ''
                        continue
            sock.close()
        except (ConnectionRefusedError, OSError) as e:
            app.logger.debug(f"Serial logger connection failed: {e}, retrying...")
        except Exception as e:
            app.logger.error(f"Serial logger error: {e}")

        # Wait before reconnecting
        _logger_stop.wait(3)


# Start the logger thread
_logger_thread = threading.Thread(target=serial_logger, daemon=True, name='serial-logger')
_logger_thread.start()


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
def get_available_ports():
    """Scan /dev for serial ports — works reliably inside Docker containers."""
    patterns = [
        '/dev/ttyUSB*',
        '/dev/ttyACM*',
        '/dev/ttyAMA*',
        '/dev/ttyS*',
        '/dev/tty.usbserial*',   # macOS
        '/dev/tty.usbmodem*',    # macOS
        '/dev/cu.usbserial*',    # macOS
        '/dev/cu.usbmodem*',     # macOS
    ]
    ports = []
    for pattern in patterns:
        ports.extend(sorted(glob.glob(pattern)))
    return ports


def get_current_port():
    if not os.path.exists(CONFIG_FILE):
        return "No configuration"
    with open(CONFIG_FILE, 'r') as f:
        for line in f:
            if 'serialdev,' in line:
                return line.split('serialdev,')[1].split(',')[0].strip()
            elif 'device:' in line:
                return line.split('device:')[1].strip().strip(',')
    return "Not configured"


def get_service_status(container_name):
    """Check service status via Docker SDK."""
    try:
        container = docker_client.containers.get(container_name)
        return container.status == 'running'
    except Exception:
        return False


def read_log_safe(filepath, max_lines=None):
    """Read a timestamped log file safely."""
    try:
        with open(filepath, 'r', errors='replace') as f:
            text = f.read()
        if max_lines:
            lines = text.strip().split('\n')
            text = '\n'.join(lines[-max_lines:])
        return text
    except Exception as e:
        return f"Error reading log: {e}"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def status():
    ports = get_available_ports()
    return jsonify({
        'current_port': get_current_port(),
        'available_ports': ports,
        'ser2net_active': get_service_status('ser2web_ser2net'),
        'ttyd_active': get_service_status('ser2web_ttyd')
    })


@app.route('/api/apply', methods=['POST'])
def apply():
    new_port = request.json.get('port')
    with open(CONFIG_FILE, 'w') as f:
        f.write("connection: &con1\n")
        f.write("  accepter: tcp,6666\n")
        f.write("  enable: on\n")
        f.write("  options:\n")
        f.write("    kickolduser: false\n")
        f.write(f"  connector: serialdev,{new_port},115200N81,local\n")

    try:
        docker_client.containers.get('ser2web_ser2net').restart(timeout=10)
        docker_client.containers.get('ser2web_ttyd').restart(timeout=10)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs/live')
def logs_live():
    text = read_log_safe(LOG_FILE, max_lines=50)
    return jsonify({'html': conv.convert(text, full=False)})


@app.route('/api/logs/archives')
def list_archives():
    files = []
    for f in os.listdir(LOG_DIR):
        if f.startswith('esp32_serial.log'):
            filepath = os.path.join(LOG_DIR, f)
            size = os.path.getsize(filepath)
            if size > 0:
                files.append({'name': f, 'size': size})
    current = [f for f in files if f['name'] == 'esp32_serial.log']
    rotated = sorted(
        [f for f in files if f['name'] != 'esp32_serial.log'],
        key=lambda x: x['name'],
        reverse=True
    )
    return jsonify({'archives': current + rotated})


@app.route('/api/logs/read/<filename>')
def read_archive(filename):
    if '/' in filename or '..' in filename:
        return jsonify({'html': 'Invalid filename'}), 400
    filepath = os.path.join(LOG_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({'html': 'File not found'}), 404
    text = read_log_safe(filepath)
    return jsonify({'html': conv.convert(text, full=False)})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)