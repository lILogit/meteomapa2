"""Application settings, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    center_lat: float = 48.9086
    center_lon: float = 14.5948
    radius_km: float = 12.0
    history_frames: int = 12
    include_forecast: bool = True

    refresh_seconds: int = 300
    status_refresh_seconds: int = 60
    cache_ttl_listing: int = 60

    cache_dir: str = "./data_cache"
    chmi_opendata: str = "https://opendata.chmi.cz/meteorology/weather/radar/composite"

    port: int = 8000
    log_level: str = "INFO"

    # --- Telegram bot + background monitor (all optional) ---
    # Empty token => bot & proactive monitoring disabled (web app still works).
    telegram_bot_token: str = ""
    # If set (>0), only this Telegram chat/account may control the bot. 0 = allow anyone.
    telegram_chat_id: int = 0

    @field_validator("telegram_chat_id", mode="before")
    @classmethod
    def _coerce_chat_id(cls, v):
        """Be tolerant of a non-numeric chat id (treat as 'allow anyone')."""
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return 0
    # Seconds between proactive checks of every active subscription.
    telegram_monitor_interval: int = 120
    # Min seconds between non-escalating notifications for the same chat.
    telegram_notify_cooldown: int = 600
    # Resolve place names (e.g. "home", "České Budějovice") via Nominatim when
    # not a saved name. Disable for offline / privacy.
    geocode_enabled: bool = True
    subscriptions_file: str = "./data_cache/subscriptions.json"
    named_locations_file: str = "./data_cache/named_locations.json"
    chat_settings_file: str = "./data_cache/chat_settings.json"

    # --- Radar composite georeferencing (verified from CHMI radar-main.js) ---
    # Geographic bounds of the 680x460 composite PNG, EPSG:3857 (Web Mercator).
    bounds_north: float = 52.167
    bounds_south: float = 48.047
    bounds_west: float = 11.267
    bounds_east: float = 20.770
    img_w: int = 680
    img_h: int = 460

    @property
    def obs_png_url(self) -> str:
        return f"{self.chmi_opendata}/pseudocappi2km/png"

    @property
    def fct_tar_url(self) -> str:
        return f"{self.chmi_opendata}/fct_pseudocappi2km/png"


@lru_cache
def get_settings() -> Settings:
    return Settings()
