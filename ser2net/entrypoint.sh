#!/bin/sh
# ser2net entrypoint: sets low_latency UART mode for reduced kernel buffer bloat,
# then launches ser2net. low_latency tells the kernel to flush the UART receive
# buffer immediately instead of batching, preventing data accumulation that can
# cause the "silent serial port" symptom.

set -e

log() {
    echo "[entrypoint] $*"
}

# Extract serial device path from ser2net.yaml and apply low_latency.
# We parse the connector line: "connector: serialdev,/dev/xxx,..."
SERIAL_DEV=""
YAML_FILE="/data/ser2net.yaml"
RUNTIME_YAML="/tmp/ser2net-runtime.yaml"
SER2NET_PORT="${SER2NET_PORT:-6666}"

mkdir -p /data

if [ ! -f "$YAML_FILE" ]; then
    log "No ser2net config found; writing a disabled default config."
    cat > "$YAML_FILE" <<'EOF'
%YAML 1.1
---
define: &confver 1.0
connection: &con1
  accepter: tcp,6666
  enable: off
  timeout: 0
EOF
fi

if [ -f "$YAML_FILE" ]; then
    SERIAL_DEV=$(grep 'serialdev,' "$YAML_FILE" | sed 's/.*serialdev,\(\/dev\/[^,]*\).*/\1/' | head -1)
fi

# Clear the trace file at startup.
TRACE_FILE="/data/esp32_serial.trace"
log "Preparing trace file: $TRACE_FILE"
if ! : > "$TRACE_FILE"; then
    log "WARNING: Could not create/truncate $TRACE_FILE"
fi

if [ -n "$SERIAL_DEV" ]; then
    SERIAL_LOCK_NAME="LCK..$(basename "$SERIAL_DEV")"
    rm -f "/var/lock/$SERIAL_LOCK_NAME" "/run/lock/$SERIAL_LOCK_NAME" 2>/dev/null || true

    if [ -e "$SERIAL_DEV" ]; then
        log "Applying low_latency to $SERIAL_DEV"
        setserial "$SERIAL_DEV" low_latency 2>/dev/null || log "WARNING: setserial low_latency failed for $SERIAL_DEV"
    else
        log "WARNING: Configured serial device does not exist yet: $SERIAL_DEV"
    fi
else
    log "No serial device configured yet."
fi

write_runtime_config() {
    local enabled="off"

    if grep -Eq '^[[:space:]]*enable:[[:space:]]*on[[:space:]]*$' "$YAML_FILE" && [ -n "$SERIAL_DEV" ]; then
        enabled="on"
    fi

    {
        printf '%%YAML 1.1\n'
        printf -- '---\n'
        printf 'define: &confver 1.0\n'
        printf 'connection: &gbfc_serial\n'
        printf '  accepter: tcp,%s\n' "$SER2NET_PORT"
        printf '  enable: %s\n' "$enabled"
        printf '  timeout: 0\n'
        if [ "$enabled" = "on" ]; then
            printf '  connector: keepopen,serialdev,%s,115200n81,local\n' "$SERIAL_DEV"
        fi
        printf '  options:\n'
        printf '    trace-both: /data/esp32_serial.trace\n'
        printf '    max-connections: 3\n'
    } > "$RUNTIME_YAML"
}

write_runtime_config

# Diagnostic info to help debugging why ser2net may exit immediately.
log "Diagnostic: ser2net binary:"
if [ -x /usr/sbin/ser2net ]; then
    ls -l /usr/sbin/ser2net || true
else
    log "WARNING: /usr/sbin/ser2net not found or not executable"
fi

log "Diagnostic: /dev listing (ttyUSB*):"
ls -l /dev/ttyUSB* 2>/dev/null || log "No ttyUSB devices found"

log "Diagnostic: /data listing and permissions:"
ls -la /data 2>/dev/null || log "/data not available"

log "Diagnostic: serial lock files:"
find /var/lock /run/lock -maxdepth 1 -type f -name 'LCK..*' -print -exec sh -c 'printf "  "; cat "$1"; printf "\n"' sh {} \; 2>/dev/null \
    || log "No serial lock files found"

log "Diagnostic: content of /data/ser2net.yaml:"
if [ -f /data/ser2net.yaml ]; then
    sed -n '1,200p' /data/ser2net.yaml || true
else
    log "/data/ser2net.yaml not found"
fi

log "Diagnostic: effective runtime ser2net config:"
sed -n '1,200p' "$RUNTIME_YAML" || true

# Background task to keep a dummy connection open, to workaround the 
# issue where trace-both/trace-read won't capture data unless connected.
if grep -Eq '^[[:space:]]*enable:[[:space:]]*on[[:space:]]*$' "$RUNTIME_YAML"; then
    (
        log "Waiting for ser2net to start before launching dummy trace client..."
        sleep 3
        while true; do
            log "Starting dummy connection to 127.0.0.1:${SER2NET_PORT}"
            nc 127.0.0.1 "$SER2NET_PORT" > /dev/null || true
            sleep 5
        done
    ) &
fi

log "Starting ser2net in foreground/debug mode with config file $RUNTIME_YAML"
# -u disables UUCP serial lock files. In this Docker setup /var/lock is inside
# the container, so those locks do not protect the host from other containers;
# they can, however, make ser2net report its own PID 1 as holding the port.
exec /usr/sbin/ser2net -n -d -u -c "$RUNTIME_YAML"
