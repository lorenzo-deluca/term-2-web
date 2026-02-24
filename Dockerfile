FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y \
    ser2net \
    supervisor \
    socat \
    cron \
    logrotate \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then \
    wget -q https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 -O /usr/local/bin/ttyd; \
    elif [ "$ARCH" = "aarch64" ]; then \
    wget -q https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.aarch64 -O /usr/local/bin/ttyd; \
    else \
    echo "Unsupported architecture: $ARCH" && exit 1; \
    fi && \
    chmod +x /usr/local/bin/ttyd

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Setup Logrotate
COPY logrotate_esp32.conf /etc/logrotate.d/esp32_serial
RUN chmod 0644 /etc/logrotate.d/esp32_serial

RUN mkdir -p /app/data
RUN touch /app/data/esp32_serial.log && chmod 666 /app/data/esp32_serial.log

CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]