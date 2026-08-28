"""Minimal async Telegram bot (raw long-polling, no extra dependency).

Runs as a guest task on the FastAPI/uvicorn event loop. Long-poll ``getUpdates``
is a blocking HTTP call, so it is dispatched to a worker thread via
``asyncio.to_thread`` to avoid blocking the loop.

Activation:
  * send a location (GPS) to the bot, or
  * send a saved name ("home") or any place name (geocoded via Nominatim).

It acks with the current condition + forecast, then the Monitor (app.monitor)
pushes proactive alerts on rain/storm changes.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import requests

from . import colormap
from .geocode import geocode
from .radar import forecast_summary

log = logging.getLogger("meteomapa.telegram")

API = "https://api.telegram.org"


class TelegramBot:
    def __init__(self, token: str, cfg, store, monitor):
        self.token = token
        self.cfg = cfg
        self.store = store
        self.monitor = monitor
        self.base = f"{API}/bot{token}"
        self._offset_path = os.path.join(cfg.cache_dir, "telegram_offset.txt")
        self._offset = self._load_offset()

    # --- HTTP ----------------------------------------------------------------
    def _call(self, method: str, **params) -> dict:
        r = requests.post(f"{self.base}/{method}", data=params, timeout=35)
        r.raise_for_status()
        return r.json()

    def send_message(self, chat_id: int, text: str, html: bool = False) -> None:
        # Default to PLAIN text: most replies contain literal "<km>" placeholders or
        # user-supplied names that would break HTML parsing (Telegram 400). Only the
        # curated HELP menu is sent as HTML.
        try:
            params = dict(chat_id=chat_id, text=text, disable_web_page_preview=True)
            if html:
                params["parse_mode"] = "HTML"
            self._call("sendMessage", **params)
        except Exception as e:  # noqa: BLE001
            log.warning("sendMessage failed (%s): %s", chat_id, e)

    def _get_updates(self, offset: int, timeout: int = 25) -> list[dict]:
        r = requests.get(f"{self.base}/getUpdates",
                         params={"offset": offset, "timeout": timeout, "limit": 50},
                         timeout=timeout + 10)
        r.raise_for_status()
        return r.json().get("result", [])

    def _load_offset(self) -> int:
        try:
            with open(self._offset_path) as f:
                return int(f.read().strip() or "0")
        except (FileNotFoundError, ValueError):
            return 0

    def _save_offset(self, offset: int) -> None:
        try:
            with open(self._offset_path, "w") as f:
                f.write(str(offset))
        except OSError:
            pass

    # --- main loop -----------------------------------------------------------
    async def run(self) -> None:
        log.info("Telegram bot started")
        identified = False
        while True:
            try:
                if not identified:
                    me = await asyncio.to_thread(self._call, "getMe")
                    res = me.get("result", {})
                    log.info("Bot identity: @%s (id %s)", res.get("username"), res.get("id"))
                    identified = True
                updates = await asyncio.to_thread(self._get_updates, self._offset, 25)
                for u in updates:
                    self._offset = max(self._offset, int(u["update_id"]) + 1)
                    try:
                        self.handle(u)
                    except Exception as e:  # noqa: BLE001
                        log.warning("handle failed: %s", e)
                if updates:
                    self._save_offset(self._offset)
            except Exception as e:  # noqa: BLE001
                # 401 = invalid token; keep retrying so a fixed .env + restart recovers,
                # but surface it loudly every loop until resolved.
                log.error("Telegram error (token invalid? network?): %s", e)
                await asyncio.sleep(10)

    # --- dispatch ------------------------------------------------------------
    def handle(self, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]

        # Optional allowlist: restrict control to a single account.
        if self.cfg.telegram_chat_id and chat_id != self.cfg.telegram_chat_id:
            log.info("ignoring message from unauthorized chat %s", chat_id)
            return

        what = "location" if msg.get("location") else (msg.get("text") or "")[:40]
        log.info("handled chat %s: %s", chat_id, what)

        if msg.get("location"):
            loc = msg["location"]
            self.activate(chat_id, loc["latitude"], loc["longitude"], name="")
            return

        text = (msg.get("text") or "").strip()
        if not text:
            return

        if text.startswith("/"):
            self._command(chat_id, text)
        else:
            self._place(chat_id, text)

    # --- commands ------------------------------------------------------------
    def _command(self, chat_id: int, text: str) -> None:
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]

        if cmd in ("/start", "/help"):
            self.send_message(chat_id, HELP, html=True)
        elif cmd == "/stop":
            ok = self.store.deactivate(chat_id)
            self.send_message(chat_id, "Monitoring paused." if ok
                              else "You have no active monitoring. Send a location or a place name to start.")
        elif cmd == "/status":
            self._cmd_status(chat_id)
        elif cmd == "/radius":
            self._cmd_radius(chat_id, args)
        elif cmd == "/save":
            self._cmd_save(chat_id, args)
        elif cmd in ("/forget", "/remove"):
            self._cmd_forget(chat_id, args)
        elif cmd == "/locations":
            self._cmd_locations(chat_id)
        else:
            self.send_message(chat_id, "Unknown command. Send /help.")

    def _cmd_status(self, chat_id: int) -> None:
        sub = self.store.get_subscription(chat_id)
        if not sub:
            self.send_message(chat_id, "No point set. Send a location (📎) or a place name.")
            return
        st = self.monitor.status_at_point(sub["lat"], sub["lon"], sub["radius_km"])
        cur = st["current"]
        cat = cur["category"]
        head = (f"{colormap.CATEGORY_GLYPH[cat]} {colormap.CATEGORY_LABEL[cat]} at "
                f"{self._loc(sub)} ({sub['radius_km']:.0f} km)")
        rate = (f"{cur['intensity_mmh']:.1f} mm/h · {int(cur['coverage']*100)}% coverage"
                if cat != "clear" else "no rain right now")
        self.send_message(chat_id, "\n".join([head, rate, forecast_summary(st)]))

    def _cmd_radius(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.send_message(chat_id, "Usage: /radius <km>   e.g. /radius 8")
            return
        try:
            km = float(args[0])
            assert 1 <= km <= 50
        except Exception:  # noqa: BLE001
            self.send_message(chat_id, "Radius must be a number between 1 and 50 km.")
            return
        self.store.set_radius(chat_id, km)
        sub = self.store.get_subscription(chat_id)
        if sub:
            self.store.update(chat_id, radius_km=km)
        self.send_message(chat_id, f"Radius set to {km:.0f} km"
                          + (" (applied to your active point)." if sub else " (will apply to your next point)."))

    def _cmd_save(self, chat_id: int, args: list[str]) -> None:
        # /save <name> [lat lon]  OR  /save <name>  (uses active/last point)
        if not args:
            self.send_message(chat_id, "Usage: /save <name> [lat lon]\nExample: /save home 48.91 14.58")
            return
        name = args[0]
        if len(args) >= 3:
            try:
                lat, lon = float(args[1]), float(args[2])
            except ValueError:
                self.send_message(chat_id, "Coordinates must be numbers: /save home 48.91 14.58")
                return
        else:
            sub = self.store.get_subscription(chat_id)
            if not sub:
                self.send_message(chat_id, "Send a location first, or give coords: /save home 48.91 14.58")
                return
            lat, lon = sub["lat"], sub["lon"]
        self.store.save_named(chat_id, name, lat, lon)
        self.send_message(chat_id, f"Saved '{name}' = {lat:.4f}, {lon:.4f}. Send '{name}' to monitor there.")

    def _cmd_forget(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.send_message(chat_id, "Usage: /forget <name>")
            return
        ok = self.store.remove_named(chat_id, args[0])
        self.send_message(chat_id, f"Removed '{args[0]}'." if ok else f"No saved location '{args[0]}'.")

    def _cmd_locations(self, chat_id: int) -> None:
        named = self.store.list_named(chat_id)
        if not named:
            self.send_message(chat_id, "No saved locations yet. Use /save <name> [lat lon].")
            return
        lines = [f"• {n} — {v['lat']:.4f}, {v['lon']:.4f}" for n, v in named.items()]
        self.send_message(chat_id, "Saved locations:\n" + "\n".join(lines))

    # --- place-name / saved-name activation ---------------------------------
    def _place(self, chat_id: int, text: str) -> None:
        saved = self.store.get_named(chat_id, text)
        if saved:
            self.activate(chat_id, saved["lat"], saved["lon"], name=text,
                          display=saved.get("display", text))
            return
        if self.cfg.geocode_enabled:
            self.send_message(chat_id, f"Looking up '{text}'…")
            hit = geocode(text)
            if hit:
                lat, lon, display = hit
                self.activate(chat_id, lat, lon, name=text, display=display)
            else:
                self.send_message(chat_id, f"Couldn't find '{text}'. Try a saved name, send a 📍 location, or /save it.")
        else:
            self.send_message(chat_id, "Not a saved name. Save it first with /save, or send a 📍 location.")

    # --- activation + ack ----------------------------------------------------
    def activate(self, chat_id: int, lat: float, lon: float,
                 name: str = "", display: str = "") -> None:
        radius = self.store.preferred_radius(chat_id)
        st = self.monitor.status_at_point(lat, lon, radius)
        from .radar import severity
        cur = st["current"]
        cat = cur["category"]
        label = f"'{name}'" if name else f"{lat:.4f}, {lon:.4f}"
        if display and display != name:
            label += f" ({display})"

        self.store.set_subscription(chat_id, lat, lon, radius, name, severity(st))

        glyph = colormap.CATEGORY_GLYPH[cat]
        now_line = (f"now: {colormap.CATEGORY_LABEL[cat]}, {cur['intensity_mmh']:.1f} mm/h"
                    if cat != "clear" else "now: clear")
        self.send_message(chat_id, "\n".join([
            f"📍 Monitoring activated at {label} (radius {radius:.0f} km).",
            now_line + ".",
            forecast_summary(st),
            "",
            "I'll alert you when rain/storm approaches or intensifies. /stop to pause, /status anytime.",
        ]))

    @staticmethod
    def _loc(sub: dict) -> str:
        return f"'{sub['name']}'" if sub.get("name") else f"{sub['lat']:.4f}, {sub['lon']:.4f}"


HELP = (
    "🌦 <b>Meteomapa rain monitor</b>\n\n"
    "Activate by sending me:\n"
    "• a 📍 <b>location</b> (paper-clip → Location), or\n"
    "• a <b>place name</b> (e.g. <i>České Budějovice</i>) or a saved name (e.g. <i>home</i>).\n\n"
    "I'll confirm and then alert you when rain/storm approaches your point, with the forecast ETA.\n\n"
    "Commands:\n"
    "/status – current condition at your point\n"
    "/radius &lt;km&gt; – set detection radius (1–50)\n"
    "/save &lt;name&gt; [lat lon] – save a named point (e.g. /save home 48.91 14.58)\n"
    "/locations – list saved points\n"
    "/forget &lt;name&gt; – remove a saved point\n"
    "/stop – pause monitoring\n"
    "/help – this message"
)
