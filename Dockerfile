FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    supervisor \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Explicitly copy your verified dependencies file
COPY requirements.txt /app/requirements.txt

# Force global environment execution path installs
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . .

COPY livekit-agent.conf /etc/supervisor/conf.d/livekit-agent.conf

EXPOSE 8080 8081 8000

CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]