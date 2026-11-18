from datetime import date

import pytest


def test_policy_clock_can_be_pinned_for_demos_and_defaults_to_utc(monkeypatch):
    from assistant.config import Settings, utc_date
    monkeypatch.setenv("CONTEXTPILOT_TODAY", "2026-11-16")
    assert Settings.from_env().today() == date(2026, 11, 16)
    monkeypatch.delenv("CONTEXTPILOT_TODAY")
    assert Settings.from_env().today() == utc_date()


def test_invalid_pinned_date_fails_at_startup(monkeypatch):
    from assistant.config import Settings
    monkeypatch.setenv("CONTEXTPILOT_TODAY", "16/11/2026")
    with pytest.raises(ValueError):
        Settings.from_env()
