FROM python:3.11-slim-bookworm

# curl is required for the Docker healthcheck (GET /ping)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watchdog.py .

CMD ["python3", "-u", "watchdog.py"]
