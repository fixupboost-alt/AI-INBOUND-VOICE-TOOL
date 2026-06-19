FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    supervisor \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --upgrade -r requirements.txt

COPY . .

# ── CRITICAL FIX: Copy the process manager configuration into the include folder ──
COPY livekit-agent.conf /etc/supervisor/conf.d/livekit-agent.conf

EXPOSE 8080 8081 8000

CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]