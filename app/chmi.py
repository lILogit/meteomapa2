"""CHMI opendata client: listing parse, HTTP fetch with retry, disk cache,
forecast-tar extraction, and publish-lag-aware "latest frame" selection.

The opendata server is a plain nginx autoindex (no CORS, no JSON API), sorted
chronologically, so the latest frame is the lexically-last matching filename.
Filenames embed UTC timestamps (verified: `20260728.0000` lists as 00:00 UTC).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

log = logging.getLogger("meteomapa.chmi")

OBS_RE = re.compile(r"^pacz2gmaps3\.z_cappi020\.(\d{8})\.(\d{4})\.0\.png$")
FCT_TAR_RE = re.compile(r"^pacz2gmaps3\.fct_z_cappi020\.(\d{8})\.(\d{4})\.ft60s10\.tar$")
FCT_PNG_RE = re.compile(r"^pacz2gmaps3\.fct_z_cappi020\.(\d{8})\.(\d{4})\.(\d{2})\.png$")


def _parse_ts(date: str, hhmm: str) -> datetime:
    d = datetime.strptime(date + hhmm, "%Y%m%d%H%M")
    return d.replace(tzinfo=timezone.utc)


def _round5(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    return dt - timedelta(minutes=dt.minute % 5)


def obs_name_for(dt: datetime) -> str:
    return f"pacz2gmaps3.z_cappi020.{dt.strftime('%Y%m%d.%H%M')}.0.png"


def fct_tar_name_for(dt: datetime) -> str:
    return f"pacz2gmaps3.fct_z_cappi020.{dt.strftime('%Y%m%d.%H%M')}.ft60s10.tar"


@dataclass
class ForecastFrame:
    member: str            # extracted png filename
    valid: datetime        # valid time (UTC)
    issue: datetime        # issue = valid - lead (UTC)
    lead_min: int


@dataclass
class _Listing:
    names: list[str] = field(default_factory=list)
    fetched_at: float = 0.0


class ChmiClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.obs_url = cfg.obs_png_url
        self.fct_url = cfg.fct_tar_url
        self.cache_obs = os.path.join(cfg.cache_dir, "obs")
        self.cache_fct = os.path.join(cfg.cache_dir, "fct")
        self._obs_listing = _Listing()
        self._fct_listing = _Listing()
        self._latest_tar_members: Optional[list[ForecastFrame]] = None
        for d in (cfg.cache_dir, self.cache_obs, self.cache_fct):
            os.makedirs(d, exist_ok=True)

    # -- HTTP -----------------------------------------------------------------
    def _get_bytes(self, url: str) -> bytes:
        last = None
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=20)
                r.raise_for_status()
                return r.content
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"fetch failed {url}: {last}")

    def _fetch_listing(self, base_url: str, pattern: re.Pattern, cache: _Listing) -> list[str]:
        if cache.names and (time.time() - cache.fetched_at) < self.cfg.cache_ttl_listing:
            return cache.names
        html = self._get_bytes(base_url + "/").decode("utf-8", "ignore")
        names = re.findall(r'href="([^"]+)"', html)
        names = [n for n in names if pattern.match(n)]
        names.sort()
        cache.names = names
        cache.fetched_at = time.time()
        return names

    def listing_observed(self) -> list[str]:
        return self._fetch_listing(self.obs_url, OBS_RE, self._obs_listing)

    def listing_tars(self) -> list[str]:
        return self._fetch_listing(self.fct_url, FCT_TAR_RE, self._fct_listing)

    # -- disk cache -----------------------------------------------------------
    def _read_cache(self, path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def _write_cache(self, path: str, data: bytes) -> None:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

    # -- observed PNGs --------------------------------------------------------
    def get_observed_png(self, name: str) -> bytes:
        if not OBS_RE.match(name):
            raise ValueError(f"bad observed name {name}")
        path = os.path.join(self.cache_obs, name)
        data = self._read_cache(path)
        if data is not None:
            return data
        data = self._get_bytes(f"{self.obs_url}/{name}")
        self._write_cache(path, data)
        return data

    def latest_observed_name(self, now: Optional[datetime] = None) -> Optional[str]:
        names = self.listing_observed()
        if not names:
            return None
        available = set(names)
        now = now or datetime.now(timezone.utc)
        slot = _round5(now)
        for back in range(7):  # up to ~30 min of publish lag tolerance
            cand = obs_name_for(slot - timedelta(minutes=5 * back))
            if cand in available:
                return cand
        return names[-1]

    def observed_names_last(self, n: int) -> list[str]:
        names = self.listing_observed()
        if not names:
            return []
        latest = self.latest_observed_name()
        if latest in names:
            idx = names.index(latest)
            return names[max(0, idx - n + 1): idx + 1]
        return names[-n:]

    # -- forecast tars --------------------------------------------------------
    def _latest_tar_name(self) -> Optional[str]:
        tars = self.listing_tars()
        if not tars:
            return None
        available = set(tars)
        now = datetime.now(timezone.utc)
        slot = _round5(now)
        for back in range(7):
            cand = fct_tar_name_for(slot - timedelta(minutes=5 * back))
            if cand in available:
                return cand
        return tars[-1]

    def get_forecast_frames(self) -> list[ForecastFrame]:
        """Extract the 6 frames of the latest available forecast tar (cached)."""
        tar_name = self._latest_tar_name()
        if not tar_name:
            return []
        m = FCT_TAR_RE.match(tar_name)
        issue = _parse_ts(m.group(1), m.group(2))

        frames: list[ForecastFrame] = []
        for member in self._tar_members(tar_name):
            mm = FCT_PNG_RE.match(member)
            if not mm:
                continue
            valid = _parse_ts(mm.group(1), mm.group(2))
            lead = int(mm.group(3))
            frames.append(ForecastFrame(member=member, valid=valid, issue=issue, lead_min=lead))
        frames.sort(key=lambda f: f.lead_min)
        self._latest_tar_members = frames
        return frames

    def _tar_members(self, tar_name: str) -> list[str]:
        """Return the 6 member filenames, downloading + extracting the tar once."""
        # All 6 members must exist on disk; if any missing, (re)extract from tar.
        member_index_path = os.path.join(self.cache_fct, tar_name + ".members.json")
        idx = None
        try:
            with open(member_index_path) as f:
                idx = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        if idx:
            members = idx.get("members", [])
            if members and all(self._read_cache(os.path.join(self.cache_fct, m)) is not None for m in members):
                return members

        tar_path = os.path.join(self.cache_fct, tar_name)
        tar_bytes = self._read_cache(tar_path)
        if tar_bytes is None:
            tar_bytes = self._get_bytes(f"{self.fct_url}/{tar_name}")
            self._write_cache(tar_path, tar_bytes)

        members: list[str] = []
        try:
            tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*")
            for mem in tf.getmembers():
                if not mem.isfile():
                    continue
                base = os.path.basename(mem.name)
                if not FCT_PNG_RE.match(base):
                    continue
                data = tf.extractfile(mem).read()
                self._write_cache(os.path.join(self.cache_fct, base), data)
                members.append(base)
        except Exception as e:  # noqa: BLE001
            log.warning("tar extract failed %s: %s", tar_name, e)
            return []
        members.sort()
        with open(member_index_path, "w") as f:
            json.dump({"tar": tar_name, "members": members}, f)
        return members

    def get_forecast_png(self, member: str) -> bytes:
        if not FCT_PNG_RE.match(member):
            raise ValueError(f"bad forecast member {member}")
        path = os.path.join(self.cache_fct, member)
        data = self._read_cache(path)
        if data is not None:
            return data
        # Not extracted yet — trigger full extraction, then read.
        self.get_forecast_frames()
        data = self._read_cache(path)
        if data is None:
            raise FileNotFoundError(member)
        return data
