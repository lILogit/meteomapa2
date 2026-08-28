"""Tests for filename parsing / naming helpers in chmi.py."""
from datetime import datetime, timezone

from app.chmi import (FCT_PNG_RE, OBS_RE, _parse_ts, fct_tar_name_for,
                      obs_name_for, _round5)


def test_obs_name_format():
    dt = datetime(2026, 8, 4, 13, 10, tzinfo=timezone.utc)
    name = obs_name_for(dt)
    assert name == "pacz2gmaps3.z_cappi020.20260804.1310.0.png"
    assert OBS_RE.match(name)


def test_fct_tar_name_format():
    dt = datetime(2026, 8, 4, 13, 10, tzinfo=timezone.utc)
    assert fct_tar_name_for(dt) == "pacz2gmaps3.fct_z_cappi020.20260804.1310.ft60s10.tar"


def test_fct_png_member_parse():
    m = FCT_PNG_RE.match("pacz2gmaps3.fct_z_cappi020.20260804.1340.30.png")
    assert m
    valid = _parse_ts(m.group(1), m.group(2))
    assert valid == datetime(2026, 8, 4, 13, 40, tzinfo=timezone.utc)
    assert int(m.group(3)) == 30  # lead minutes


def test_round5():
    dt = datetime(2026, 8, 4, 13, 7, 33, tzinfo=timezone.utc)
    assert _round5(dt).minute == 5
