FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ scripts/
COPY server/ server/
COPY GOALS.md .

# Persistent state (database + Garmin token cache) lives on a mounted volume.
ENV DB_PATH=/data/activities.db \
    GARMINTOKENS=/data/garminconnect

EXPOSE 8000

CMD ["python", "server/mcp_server.py"]
