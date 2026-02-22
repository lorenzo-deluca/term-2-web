FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y \
    ser2net \
    ttyd \
    supervisor \
    telnet \
    cron \
    logrotate \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Setup Logrotate
COPY logrotate_esp32.conf /etc/logrotate.d/esp32_serial
RUN chmod 0644 /etc/logrotate.d/esp32_serial

RUN mkdir -p /data
RUN touch /data/esp32_serial.log && chmod 666 /data/esp32_serial.log

CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]