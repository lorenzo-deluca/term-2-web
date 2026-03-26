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
    SERIAL_DEV=$(grep -oP '(?<=serialdev,)/dev/[^,]+' "$YAML_FILE" | head -1)
fi

if [ -n "$SERIAL_DEV" ] && [ -e "$SERIAL_DEV" ]; then
    echo "[entrypoint] Setting low_latency on $SERIAL_DEV"
    setserial "$SERIAL_DEV" low_latency 2>/dev/null || \
        echo "[entrypoint] WARNING: setserial low_latency failed (non-fatal)"
else
    echo "[entrypoint] Serial device not found or not configured yet — skipping low_latency setup"
fi

echo "[entrypoint] Starting ser2net..."
exec /usr/sbin/ser2net -n -c /data/ser2net.yaml
