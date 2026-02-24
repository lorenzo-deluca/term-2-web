FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y \
    cron \
    logrotate \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY logrotate_esp32.conf /etc/logrotate.d/esp32_serial
RUN chmod 0644 /etc/logrotate.d/esp32_serial

COPY . .

CMD cron && python3 app.py