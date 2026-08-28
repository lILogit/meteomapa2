# METEOMAPA — single-process FastAPI service (uvicorn + in-loop Telegram bot/monitor).
# One container: uvicorn serves the web API/static frontend, the CHMI cache refresher,
# the Telegram long-poll bot and the proactive monitor all run as in-process async tasks.
# (Golden Rule #1: one process, one datastore. No worker, no broker, no second service.)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Pillow/numpy ship manylinux wheels (no -dev libs needed). tzdata keeps the
# Europe/Prague display timestamps correct (TZ).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps first → cache layer survives code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# App code + static frontend + build scripts.
COPY app/ ./app/
COPY static/ ./static/
COPY scripts/ ./scripts/

# Vendor the CHMI legend/borders/orography + transparent placeholder into the image
# at build time, so the browser never hits produkty.chmi.cz directly at runtime (CORS).
RUN python scripts/fetch_assets.py

# Non-root runtime. /data is the persisted volume (CACHE_DIR=/data): subscriptions,
# named locations, telegram offset and the radar frame cache survive recreate.
RUN useradd --create-home --uid 1000 meteomapa \
    && mkdir -p /data && chown -R meteomapa:meteomapa /app /data
USER meteomapa

EXPOSE 8000

# Single uvicorn process. The bot/monitor/refresher run in-process — no separate worker.
# --proxy-headers: trust the Traefik reverse proxy's X-Forwarded-* headers.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
