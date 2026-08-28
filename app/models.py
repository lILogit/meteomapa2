"""Pydantic response models for the API contract."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Center(BaseModel):
    lat: float
    lon: float


class FrameRef(BaseModel):
    time: str
    url: str
    is_latest: Optional[bool] = None


class ForecastRef(BaseModel):
    time: str
    lead_min: int
    issue: str
    url: str


class FramesResponse(BaseModel):
    center: Center
    generated_at: str
    observed: list[FrameRef]
    forecast: list[ForecastRef]
    latest_observed_time: Optional[str]
    forecast_issue_time: Optional[str]
    refresh_seconds: int
    degraded: bool = False


class TimelineEntry(BaseModel):
    time: str
    kind: str
    lead_min: Optional[int]
    category: str
    intensity_mmh: float
    dbz: Optional[float]
    coverage: float


class NextEvent(BaseModel):
    type: str
    time: str
    in_min: int


class Verdict(BaseModel):
    status: str
    label: str
    detail: str
    next_event: Optional[NextEvent]


class StatusResponse(BaseModel):
    center: Center
    radius_km: float
    sampled_at: str
    latest_observed_time: Optional[str]
    timeline: list[TimelineEntry]
    current: TimelineEntry
    verdict: Verdict
    category_meta: dict
    degraded: bool = False


class HealthResponse(BaseModel):
    status: str
    latest_observed: Optional[str]
    cache_ok: bool
