#!/bin/sh
# ser2net entrypoint: sets low_latency UART mode for reduced kernel buffer bloat,
# then launches ser2net. low_latency tells the kernel to flush the UART receive
# buffer immediately instead of batching, preventing data accumulation that can
# cause the "silent serial port" symptom.

set -e

# Extract serial device path from ser2net.yaml and apply low_latency.
# We parse the connector line: "connector: serialdev,/dev/xxx,..."
SERIAL_DEV=""
YAML_FILE="/data/ser2net.yaml"

if [ -f "$YAML_FILE" ]; then
    SERIAL_DEV=$(grep 'serialdev,' "$YAML_FILE" | sed 's/.*serialdev,\(\/dev\/[^,]*\).*/\1/' | head -1)
fi

# Clear the trace file at startup
TRACE_FILE="/data/esp32_serial.trace"
if [ -f "$TRACE_FILE" ]; then
    echo "[entrypoint] Clearing trace file: $TRACE_FILE"
    > "$TRACE_FILE"
fi


# Diagnostic info to help debugging why ser2net may exit immediately.
echo "[entrypoint] Diagnostic: ser2net binary:" 
if [ -x /usr/sbin/ser2net ]; then
    ls -l /usr/sbin/ser2net || true
else
    echo "[entrypoint] WARNING: /usr/sbin/ser2net not found or not executable"
fi

echo "[entrypoint] Diagnostic: /dev listing (ttyUSB*):"
ls -l /dev/ttyUSB* 2>/dev/null || echo "[entrypoint] No ttyUSB devices found"

echo "[entrypoint] Diagnostic: /data listing and permissions:"
ls -la /data 2>/dev/null || echo "[entrypoint] /data not available"

echo "[entrypoint] Diagnostic: content of /data/ser2net.yaml (if present):"
if [ -f /data/ser2net.yaml ]; then
    sed -n '1,200p' /data/ser2net.yaml || true
else
    echo "[entrypoint] /data/ser2net.yaml not found"
fi

# Background task to keep a dummy connection open, to workaround the 
# issue where trace-both/trace-read won't capture data unless connected.
(
    echo "[entrypoint] Waiting for ser2net to start before launching dummy client..."
    sleep 3
    while true; do
        echo "[entrypoint] Starting dummy connection to 127.0.0.1:6666"
        nc 127.0.0.1 6666 > /dev/null
        sleep 5
    done
) &

echo "[entrypoint] Starting ser2net in debug mode..."
# Use -Y to supply YAML configuration (ser2net requires -Y for YAML input).
# This ensures options like `trace-read` are understood and ser2net will
# perform reading from the serial device continuously even without active
# TCP clients.
if [ -f /data/ser2net.yaml ]; then
    # Read the YAML file content and pass as a -Y argument. Surrounding
    # with single quotes avoids word-splitting; we use cat to preserve
    # newlines which ser2net expects.
    exec /usr/sbin/ser2net -n -d -Y "$(cat /data/ser2net.yaml)"
else
    exec /usr/sbin/ser2net -n -d
fi
