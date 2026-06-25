FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create a non-root user and a writable data dir for the managed server list
RUN useradd -m appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data
USER appuser

# Persisted server registry (mount a volume at /data to keep it across restarts)
VOLUME ["/data"]

# Set environment variables
ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1
# Bind to all interfaces so the app is reachable through the published port.
ENV HOST=0.0.0.0
ENV PORT=5000
# Persist the managed server list outside the image layer (mount a volume here).
ENV WEBOLLAMA_SERVERS_FILE=/data/servers.json

# Expose the port
EXPOSE 5000

# Start the application
CMD ["python", "app.py"]
