import os
import subprocess
from flask import Flask, request, render_template, jsonify
import serial.tools.list_ports
from ansi2html import Ansi2HTMLConverter

app = Flask(__name__)
CONFIG_FILE = './data/ser2net.yaml'
LOG_DIR = './data'
LOG_FILE = f'{LOG_DIR}/esp32_serial.log'
conv = Ansi2HTMLConverter(dark_bg=True)

if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'w') as f:
        f.write(f"connection: &con1\n  accepter: tcp,3333\n  enable: off\n  options:\n    trace-read: {LOG_FILE}\n    trace-write: {LOG_FILE}\n  connector: serialdev,/dev/null,115200N81,local # device:None\n")

def get_current_port():
    if not os.path.exists(CONFIG_FILE): return "No configuration"
    with open(CONFIG_FILE, 'r') as f:
        for line in f:
            if 'device:' in line: return line.split('device:')[1].strip().strip(',')
    return "Not detected"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def status():
    ports = [port.device for port in serial.tools.list_ports.comports()]
    snet = subprocess.run(['supervisorctl', '-c', 'supervisord.conf', 'status', 'ser2net'], capture_output=True, text=True)
    ttyd = subprocess.run(['supervisorctl', '-c', 'supervisord.conf', 'status', 'ttyd'], capture_output=True, text=True)
    return jsonify({
        'current_port': get_current_port(),
        'available_ports': ports,
        'ser2net_active': 'RUNNING' in snet.stdout,
        'ttyd_active': 'RUNNING' in ttyd.stdout
    })

@app.route('/api/apply', methods=['POST'])
def apply():
    new_port = request.json.get('port')
    yaml_content = f"connection: &con1\n  accepter: tcp,3333\n  enable: on\n  options:\n    kickolduser: true\n    trace-read: {LOG_FILE}\n    trace-write: {LOG_FILE}\n  connector: serialdev,{new_port},115200N81,local # device:{new_port}\n"
    with open(CONFIG_FILE, 'w') as f: f.write(yaml_content)
    subprocess.run(['supervisorctl', '-c', 'supervisord.conf', 'restart', 'ser2net'], check=True)
    subprocess.run(['supervisorctl', '-c', 'supervisord.conf', 'restart', 'ttyd'], check=True)
    return jsonify({'success': True})

@app.route('/api/logs/live')
def logs_live():
    res = subprocess.run(['tail', '-n', '50', LOG_FILE], capture_output=True, text=True)
    html_logs = conv.convert(res.stdout, full=False)
    return jsonify({'html': html_logs})

@app.route('/api/logs/archives')
def list_archives():
    files = [f for f in os.listdir(LOG_DIR) if f.startswith('esp32_serial.log_')]
    files.sort(reverse=True)
    return jsonify({'archives': files})

@app.route('/api/logs/read/<filename>')
def read_archive(filename):
    if not filename.startswith('esp32_serial.log_'): return jsonify({'html': 'Invalid file'}), 400
    filepath = os.path.join(LOG_DIR, filename)
    if not os.path.exists(filepath): return jsonify({'html': 'File not found'}), 404
    with open(filepath, 'r') as f:
        data = f.read()
    return jsonify({'html': conv.convert(data, full=False)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)