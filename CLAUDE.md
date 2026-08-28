# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is
**Meteomapa** — a small web app + Telegram bot that shows the live CHMI (ČHMÚ)
weather radar, detects rain/storm at a chosen point, shows an observed+forecast
timeline, and pushes proactive Telegram alerts. Python/FastAPI backend, static
Leaflet frontend. Single process, no DB, no external JS framework.

## Run / test
```bash
./run.sh                                 # venv + deps + vendor assets + uvicorn (reads PORT from .env)
.venv/bin/python -m pytest -q            # unit tests (georef, detect, monitor, chmi parsing)
.venv/bin/python scripts/fetch_assets.py # re-vendor legend/borders/oro from CHMI
.venv/bin/python scripts/build_colormap.py # print the RGB→dBZ LUT (compare vs app/colormap.py)
```
- Web app: `http://localhost:<PORT>` (PORT in `.env`; defaults to 8000).
- Config: copy `.env.example` → `.env`. Key vars: `CENTER_LAT/LON`, `RADIUS_KM`,
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEOCODE_ENABLED`.

## Architecture
Backend (`app/`) — one FastAPI process; async background tasks share the uvicorn loop:
- `main.py` — endpoints + startup tasks (cache refresher, telegram bot, monitor loop).
- `chmi.py` — CHMI opendata client: nginx-autoindex listing parse, disk cache
  (immutable filenames), forecast-tar extraction, publish-lag-aware "latest frame".
- `georef.py` — Web Mercator + lat/lon→pixel + radius disk mask (pure math, no GIS lib).
- `colormap.py` — 17-entry RGB→dBZ→mm/h table (hard-coded; dBZ→mm/h via Marshall–Palmer).
- `detect.py` — sample a frame's pixels in a mask, classify intensity, build timeline, verdict.
- `radar.py` — `status_at(point)` shared by the `/api/status` endpoint AND the monitor;
  also `severity()` + `forecast_summary()` for notifications.
- `monitor.py` — `Store` (JSON-backed subscriptions + named locations) + `Monitor`
  (notification state machine: escalate immediately, throttle improvements).
- `telegram_bot.py` — raw long-poll bot (no webhook/public URL needed). Activation by
  GPS, saved name, or geocoded place; acks then relies on the monitor to push alerts.
- `geocode.py` — place name → coords via OSM Nominatim (toggle via `GEOCODE_ENABLED`).
- `models.py`, `config.py` — pydantic response models / settings (`.env`).

Frontend (`static/`) — Leaflet 1.9 (CDN), vanilla JS:
- `index.html`, `css/style.css`, `js/app.js` (map, status panel, polls `/api/config`+`/api/status`),
  `js/timeline.js` (scrubber/animation). Center comes from `/api/config` (single source of truth = `.env`);
  the ✛ marker is draggable / click-to-set.

API: `GET /api/health`, `/api/config`, `/api/frames`, `/api/status?lat=&lon=&radius_km=`,
`/api/img/{obs|fct}/{filename}` (strict-regex image proxy).

## Key technical facts (verified — do not re-derive unless something breaks)
**Observed radar** (5-min updates, UTC timestamps):
`https://opendata.chmi.cz/meteorology/weather/radar/composite/pseudocappi2km/png/pacz2gmaps3.z_cappi020.YYYYMMDD.HHMM.0.png`
— PNG **680×460 RGBA**, **transparent except rain cells**; only ~3% of pixels opaque.

**Forecast (nowcast)**: `.../fct_pseudocappi2km/png/pacz2gmaps3.fct_z_cappi020.YYYYMMDD.HHMM.ft60s10.tar`
— one tar per 5-min issue, contains **6 frames** at lead +10..+60 min
(member name `….<VALID_YYYYMMDD.HHMM>.<LEADMIN>.png`, LEADMIN = minutes).

**Georeferencing** (from `produkty.chmi.cz/radar/js/radar-main.js:1633`): bounds
**N 52.167 / S 48.047 / W 11.267 / E 20.770**, image in **Web Mercator (EPSG:3857)**.
To sample a point: project bounds corners **and** the target to Mercator, then
linear-interpolate to pixels (image row 0 = north). Interpolating raw lat/lon is wrong.

**Detection keys on alpha only** (`alpha > 0` = precipitation). This is what resolves
the transparent-black `(0,0,0)` / legend ambiguity. Category by 95th-percentile peak dBZ:
clear <12, light 12–24, moderate 25–37, heavy 38–47, storm ≥48.

**opendata.chmi.cz has no CORS headers** → the backend proxy is mandatory; browsers
cannot fetch these images directly. Cache by immutable filename (no TTL); only the
directory listing has a TTL (~60 s). CHMI keeps ~2175 frames (~7.5 days).

## Conventions
- Keep `status_at()` in `radar.py` the single path for point evaluation (endpoint + monitor).
- Filenames are UTC; display in Europe/Prague (`zoneinfo`); server clock uses `datetime.now(timezone.utc)`.
- All CHMI filenames validated by strict regex before any I/O (path-traversal defense).
- Atomic file writes via temp + `os.replace`.
- Match surrounding code style: type hints, `from __future__ import annotations`, small focused modules.

## Gotchas
- **Port 8000 is taken** on this machine by another project ("Cortex"); Meteomapa runs on 8765 (`PORT` in `.env`). Don't kill the Cortex process.
- The bot's `run()` retries forever on a bad token (logs `401` every 10 s) rather than crashing — a fixed `.env` + restart recovers.
- `TELEGRAM_CHAT_ID` must be the user's **numeric** account id (get via @userinfobot), not the bot id (the token prefix). A non-numeric value is coerced to 0 (allow all) so the app won't crash.
- Keep secrets in `.env` (gitignored), not `.env.example` (the shareable template).
- The radar image bounds in `app.js`/`config.py` are `[[S,W],[N,E]]` = `[[48.047,11.267],[52.167,20.770]]`.
