"""Tests for the notification state machine + message building (no network)."""
from app.config import get_settings
from app.monitor import Monitor, Store, build_notify_message
from app.radar import cat_rank, forecast_summary, severity

cfg = get_settings()
store = Store(cfg)
mon = Monitor(cfg, store, client=None, mapping=None, send=lambda *_a, **_k: None)


def _status(cur_cat="clear", verdict_status="clear", onset=None, clearing=None):
    cur = {"time": "2026-08-04T13:00:00Z", "kind": "observed", "lead_min": None,
           "category": cur_cat, "intensity_mmh": 0.0 if cur_cat == "clear" else 3.0,
           "dbz": None if cur_cat == "clear" else 30, "coverage": 0.0 if cur_cat == "clear" else 0.1}
    fct = []
    if onset:
        fct.append({"time": "2026-08-04T13:30:00Z", "kind": "forecast", "lead_min": 30,
                    "category": onset, "intensity_mmh": 2.0, "dbz": 28, "coverage": 0.1})
    if clearing:
        fct.append({"time": "2026-08-04T14:00:00Z", "kind": "forecast", "lead_min": 60,
                    "category": "clear", "intensity_mmh": 0.0, "dbz": None, "coverage": 0.0})
    return {"current": cur, "timeline": fct, "verdict": {"status": verdict_status, "label": "", "detail": "", "next_event": None}}


def test_severity_levels():
    assert severity(_status("clear", "clear")) == 0
    assert severity(_status("clear", "rain_approaching")) == 1
    assert severity(_status("light", "rain_now")) == 1
    assert severity(_status("moderate", "rain_now")) == 2
    assert severity(_status("storm", "rain_now")) == 4


def test_should_notify_escalation_is_immediate():
    # worsening always notifies regardless of cooldown
    assert mon.should_notify(prev=0, new=1, prev_ts=0.0, now=1.0) is True
    assert mon.should_notify(prev=2, new=4, prev_ts=100.0, now=101.0) is True


def test_should_notify_throttles_improvement():
    # improvement only after cooldown
    assert mon.should_notify(prev=3, new=2, prev_ts=100.0, now=101.0) is False
    assert mon.should_notify(prev=3, new=2, prev_ts=0.0, now=cfg.telegram_notify_cooldown + 1) is True


def test_should_notify_no_change_is_silent():
    assert mon.should_notify(prev=2, new=2, prev_ts=0.0, now=9999.0) is False


def test_build_message_storm():
    sub = {"lat": 48.9, "lon": 14.6, "radius_km": 5, "name": "home"}
    msg = build_notify_message(sub, _status("storm", "rain_now"))
    assert "STORM" in msg and "home" in msg and "mm/h" in msg


def test_build_message_approaching():
    sub = {"lat": 48.9, "lon": 14.6, "radius_km": 5, "name": ""}
    msg = build_notify_message(sub, _status("clear", "rain_approaching", onset="light"))
    assert "approaching" in msg and "48.9000" in msg


def test_forecast_summary_clear_no_rain():
    s = _status("clear", "clear")
    assert "no rain expected" in forecast_summary(s)


def test_forecast_summary_with_onset_and_clearing():
    s = _status("clear", "rain_approaching", onset="moderate", clearing=True)
    out = forecast_summary(s)
    assert "expected" in out and "clearing" in out


def _store_in(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "subscriptions_file", str(tmp_path / "subs.json"))
    monkeypatch.setattr(cfg, "named_locations_file", str(tmp_path / "named.json"))
    monkeypatch.setattr(cfg, "chat_settings_file", str(tmp_path / "settings.json"))
    return Store(cfg)


def test_store_subscription_roundtrip(tmp_path, monkeypatch):
    s = _store_in(tmp_path, monkeypatch)
    s.set_subscription(123, 48.9, 14.6, 5.0, "home", current_severity=0)
    assert s.get_subscription(123)["name"] == "home"
    assert s.all_active()
    s.update(123, last_severity=2)
    assert s.get_subscription(123)["last_severity"] == 2
    assert s.deactivate(123) is True
    assert not s.all_active()


def test_store_named_locations(tmp_path, monkeypatch):
    s = _store_in(tmp_path, monkeypatch)
    s.save_named(123, "Home", 48.91, 14.58)
    got = s.get_named(123, "home")  # case-insensitive
    assert got and abs(got["lat"] - 48.91) < 1e-6
    assert s.remove_named(123, "home") is True
    assert s.get_named(123, "home") is None
