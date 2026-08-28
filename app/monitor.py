"""Background monitor: per-chat rain subscriptions + a notification state machine.

Subscriptions are persisted to JSON so they survive restarts. The monitor polls
each active point, computes the verdict, and notifies the chat on a meaningful
change — escalating immediately when rain/storm gets closer or worse, throttling
improvement/clearing messages with a cooldown so it never spams.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Optional

from . import colormap
from .radar import forecast_summary, severity, status_at

log = logging.getLogger("meteomapa.monitor")


def _atomic_write(path: str, data: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(data)
    os.replace(tmp, path)


class Store:
    """JSON-backed subscriptions + named locations."""

    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(os.path.dirname(cfg.subscriptions_file) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(cfg.named_locations_file) or ".", exist_ok=True)
        self._subs = self._load(cfg.subscriptions_file, default={})
        self._named = self._load(cfg.named_locations_file, default={})
        self._prefs = self._load(cfg.chat_settings_file, default={})

    # -- per-chat preferences (radius) --
    def preferred_radius(self, chat_id: int) -> float:
        chat = self._prefs.get(str(chat_id), {})
        if chat.get("radius_km"):
            return float(chat["radius_km"])
        existing = self._subs.get(str(chat_id))
        return existing["radius_km"] if existing else self.cfg.radius_km

    def set_radius(self, chat_id: int, km: float) -> None:
        self._prefs.setdefault(str(chat_id), {})["radius_km"] = km
        _atomic_write(self.cfg.chat_settings_file, json.dumps(self._prefs, indent=2))

    @staticmethod
    def _load(path: str, default):
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    # -- subscriptions --
    def set_subscription(self, chat_id: int, lat: float, lon: float,
                         radius_km: float, name: str, current_severity: int = 0) -> dict:
        prev = self._subs.get(str(chat_id), {})
        sub = {
            "chat_id": chat_id, "lat": lat, "lon": lon,
            "radius_km": radius_km, "name": name, "active": True,
            # seed the state machine with the current severity so we only notify on CHANGE
            "last_severity": current_severity,
            "last_notified_ts": 0.0,
        }
        self._subs[str(chat_id)] = sub
        self._save_subs()
        return sub

    def get_subscription(self, chat_id: int) -> Optional[dict]:
        return self._subs.get(str(chat_id))

    def all_active(self) -> list[dict]:
        return [s for s in self._subs.values() if s.get("active")]

    def deactivate(self, chat_id: int) -> bool:
        s = self._subs.get(str(chat_id))
        if not s:
            return False
        s["active"] = False
        self._save_subs()
        return True

    def update(self, chat_id: int, **fields) -> None:
        s = self._subs.get(str(chat_id))
        if s:
            s.update(fields)
            self._save_subs()

    def _save_subs(self) -> None:
        _atomic_write(self.cfg.subscriptions_file, json.dumps(self._subs, indent=2))

    # -- named locations --
    def save_named(self, chat_id: int, name: str, lat: float, lon: float, display: str = "") -> None:
        self._named.setdefault(str(chat_id), {})[name.lower()] = {
            "lat": lat, "lon": lon, "display": display or name,
        }
        _atomic_write(self.cfg.named_locations_file, json.dumps(self._named, indent=2))

    def get_named(self, chat_id: int, name: str) -> Optional[dict]:
        return self._named.get(str(chat_id), {}).get(name.lower())

    def list_named(self, chat_id: int) -> dict:
        return self._named.get(str(chat_id), {})

    def remove_named(self, chat_id: int, name: str) -> bool:
        bucket = self._named.get(str(chat_id), {})
        if name.lower() in bucket:
            del bucket[name.lower()]
            _atomic_write(self.cfg.named_locations_file, json.dumps(self._named, indent=2))
            return True
        return False


class Monitor:
    def __init__(self, cfg, store: Store, client, mapping, send: Callable[[int, str], None]):
        self.cfg = cfg
        self.store = store
        self.client = client
        self.mapping = mapping
        self.send = send

    def status_for(self, sub: dict) -> Optional[dict]:
        try:
            return status_at(self.client, self.mapping, self.cfg,
                             sub["lat"], sub["lon"], sub["radius_km"])
        except Exception as e:  # noqa: BLE001
            log.warning("status_for failed for %s: %s", sub.get("chat_id"), e)
            return None

    def status_at_point(self, lat: float, lon: float, radius_km: float) -> Optional[dict]:
        return status_at(self.client, self.mapping, self.cfg, lat, lon, radius_km)

    def should_notify(self, prev: int, new: int, prev_ts: float, now: float) -> bool:
        if new == prev:
            return False
        if new > prev:                       # escalation: always alert
            return True
        return (now - prev_ts) >= self.cfg.telegram_notify_cooldown  # improvement: throttle

    async def check_all(self) -> None:
        now = time.time()
        for sub in self.store.all_active():
            chat_id = sub["chat_id"]
            st = self.status_for(sub)
            if not st:
                continue
            sev = severity(st)
            prev = sub.get("last_severity", 0)
            prev_ts = sub.get("last_notified_ts", 0.0)
            if self.should_notify(prev, sev, prev_ts, now):
                try:
                    self.send(chat_id, build_notify_message(sub, st))
                    self.store.update(chat_id, last_severity=sev,
                                      last_notified_ts=now,
                                      last_status=st["verdict"]["status"])
                except Exception as e:  # noqa: BLE001
                    log.warning("notify failed for %s: %s", chat_id, e)


def _loc(sub: dict) -> str:
    name = sub.get("name") or ""
    return f"'{name}'" if name else f"{sub['lat']:.4f}, {sub['lon']:.4f}"


def build_notify_message(sub: dict, st: dict) -> str:
    cur = st["current"]
    v = st["verdict"]
    cat = cur["category"]
    loc = _loc(sub)
    glyph = colormap.CATEGORY_GLYPH.get(cat, "🌧")
    label = colormap.CATEGORY_LABEL.get(cat, "Rain")

    if v["status"] == "rain_approaching":
        head = f"🌧 Rain approaching {loc}"
    elif cat == "storm":
        head = f"⛈ STORM at {loc}!"
    elif v["status"] == "rain_now":
        head = f"{glyph} {label} at {loc}"
    else:
        head = f"☀ Cleared at {loc}"

    parts = [head]
    if cat != "clear":
        parts.append(f"{cur['intensity_mmh']:.1f} mm/h · {int(cur['coverage'] * 100)}% coverage")
    parts.append(forecast_summary(st))
    return "\n".join(parts)
