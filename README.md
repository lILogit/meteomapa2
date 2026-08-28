# Meteomapa

Live **CHMI (ČHMÚ) weather radar** map centered on a point, with **automatic
rain-at-center detection** and an animated **observed + forecast timeline**
(+10…+60 min nowcast).

The radar imagery comes from CHMI's open-data composites
(`opendata.chmi.cz/.../pseudocappi2km`) — the same source as
[`produkty.chmi.cz/radar/`](https://produkty.chmi.cz/radar/). Because that server
sends no CORS headers, a small Python backend proxies the images and performs the
georeferenced pixel sampling that powers the "is it raining at the center?"
indicator.

## What it does
- Shows the radar reflectivity overlay on an OpenStreetMap base, centered on your location.
- **Indicates rain at/near the center** with intensity (clear / light / moderate / heavy / storm), rate (mm/h) and coverage (%).
- Forecasts onset/clearing from the CHMI nowcast: *"Rain expected at 13:35 (~25 min)"*.
- Animated timeline scrubber across observed history → forecast (+10…+60 min).

## Run (manual / dev server)
```bash
./run.sh
# → http://localhost:8000
```
`run.sh` creates a `.venv`, installs `requirements.txt`, vendors the legend/borders
images, and starts uvicorn. Configure via `.env` (copy from `.env.example`):

| Var | Default | Meaning |
|---|---|---|
| `CENTER_LAT` / `CENTER_LON` | `48.9086` / `14.5948` | detection center |
| `RADIUS_KM` | `12` | sampling radius |
| `HISTORY_FRAMES` | `12` | past observed 5-min frames |
| `INCLUDE_FORECAST` | `true` | +10…+60 min nowcast |
| `PORT` | `8000` | |

## How rain detection works
The composite PNG (680×460, Web Mercator, bounds N52.167/S48.047/W11.267/E20.770)
is transparent everywhere except precipitation cells. The backend projects the
center lat/lon to a pixel, samples a ~12 km disk, gates on the alpha channel
(only opaque pixels count — this resolves the transparent-black ambiguity), maps
each pixel's color → dBZ via the legend table, and takes the 95th-percentile peak.
Categories by peak dBZ: clear <12, light 12–24, moderate 25–37, heavy 38–47, storm ≥48.
mm/h is estimated with Marshall–Palmer `Z = 200·R^1.6`.

## API
- `GET /api/health` — liveness + latest frame.
- `GET /api/frames?history=12&forecast=true` — available observed + forecast frames.
- `GET /api/status?lat=..&lon=..&radius_km=12` — detection timeline + verdict.
- `GET /api/img/{obs|fct}/{filename}` — cached image proxy.

## Telegram alerts (background monitoring)
Optional: a built-in bot monitors your points in the background and messages you
on Telegram when rain/storm approaches or intensifies, with the forecast ETA.

### Enable
1. Create a bot: message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Put it in `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=123456:ABC...
   ```
3. Restart (`./run.sh`). On startup you'll see `Telegram bot + monitor enabled`.

### Use
Open your bot in Telegram and:
- **Send a location** (📎 → Location) to monitor that GPS point, **or**
- **Send a place name** (e.g. `České Budějovice`, geocoded via OpenStreetMap) **or a saved name** (e.g. `home`).
- The bot replies with an **ack**: current condition + forecast at that point.

It then alerts you automatically — e.g. `🌧 Rain approaching 'home' — Moderate rain expected ~15:30 (+30 min), clearing ~16:00`, or `⛈ STORM at 'home'! 36 mm/h, clearing in ~29 min`. Alerts escalate immediately when things worsen; improvement/clearing messages are throttled (cooldown) so it never spams.

### Commands
| Command | Action |
|---|---|
| `/status` | current condition at your point |
| `/radius <km>` | set detection radius (1–50) |
| `/save <name> [lat lon]` | save a named point (e.g. `/save home 48.91 14.58`) |
| `/locations` | list saved points |
| `/forget <name>` | remove a saved point |
| `/stop` | pause monitoring |
| `/help` | help |

Subscriptions and saved locations persist to `data_cache/` (survive restarts). Tune via `.env`: `TELEGRAM_MONITOR_INTERVAL`, `TELEGRAM_NOTIFY_COOLDOWN`, `GEOCODE_ENABLED`.

## Deploy (Docker + Traefik on Hostinger)

`docker-compose.yml` targets Hostinger Docker Manager, where one **Traefik** project
owns :80/:443 for the whole VPS and every app joins its external network
`traefik-proxy`. Meteomapa publishes no public ports — Traefik discovers it through
the Docker socket and routes `Host(DOMAIN_NAME)` to the container's :8000, with
Let's Encrypt TLS (`letsencrypt` resolver, `websecure` entrypoint).

```bash
cp .env.example .env          # set DOMAIN_NAME, SSL_EMAIL, TELEGRAM_* ...
docker compose up -d --build  # alongside the Hostinger Traefik project
```

Point the `DOMAIN_NAME` A record at the VPS **before** the first request; the ACME
TLS challenge fails otherwise.

On a VPS without that template, start the bundled proxy instead (same entrypoint and
resolver names, so the app labels are unchanged):

```bash
docker network create traefik-proxy            # once
docker compose --profile standalone up -d --build
```

Only one Traefik per host may bind :80/:443. If another project already runs one,
use the shared-proxy form above and let it route Meteomapa by its own `Host()` rule.
`127.0.0.1:8765` stays mapped for SSH-tunnel debugging; drop that `ports:` entry for
zero host ports. State (frame cache, subscriptions, named locations) lives in the
`data` volume at `/data`.

## Tests
```bash
.venv/bin/python -m pytest -q
```

## Notes / limits
- CHMI publishes a new frame every 5 min (~2–4 min lag); the backend walks back to the latest available slot.
- Filenames are UTC; the UI shows Europe/Prague time.
- Frames are cached permanently by immutable filename; only the directory listing is polled (≤1/min).
- Data © CHMI; map © OpenStreetMap contributors.
