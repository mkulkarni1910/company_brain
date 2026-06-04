import pytest
from app.config import get_settings


def test_bot_config_defaults_to_none(monkeypatch):
    get_settings.cache_clear()
    for k in ("TEAMS_BOT_APP_ID", "TEAMS_BOT_APP_PASSWORD", "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"):
        monkeypatch.delenv(k, raising=False)
    s = get_settings()
    assert s.teams_bot_app_id is None
    assert s.teams_bot_app_password is None
    assert s.slack_bot_token is None
    assert s.slack_signing_secret is None


def test_bot_config_reads_from_env(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("TEAMS_BOT_APP_ID", "my-app-id")
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", "my-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "signingsecret")
    s = get_settings()
    assert s.teams_bot_app_id == "my-app-id"
    assert s.teams_bot_app_password == "my-secret"
    assert s.slack_bot_token == "xoxb-123"
    assert s.slack_signing_secret == "signingsecret"
