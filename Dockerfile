# Use an explicit, stable Python runtime base image
FROM python:3.11-slim

# Install system utilities needed by livekit plugins natively
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    supervisor \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set up the application execution directory
WORKDIR /app

# Copy dependency configuration directly to workspace root
COPY requirements.txt .

# Force fresh package builds directly inside global runtime paths without caching
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --upgrade -r requirements.txt

# Copy all remaining source files (agent.py, configuration settings)
COPY . .

# Expose livekit framework operational monitoring ports
EXPOSE 8080 8081 8000

# Fire up the default daemon supervisor process layout
CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]