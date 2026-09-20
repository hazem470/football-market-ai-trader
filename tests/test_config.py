"""Configuration: precedence, validation and never leaking a secret."""
from __future__ import annotations

import pytest

from src.config.settings import ConfigError, Secrets, load_settings


def test_shipped_config_loads_and_is_valid(config_path, tmp_path):
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    assert settings.validate() == []


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(config_path=tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("this: is: not: valid: yaml: [", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(config_path=bad)


def test_non_mapping_root_raises(tmp_path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(config_path=bad)


def test_env_overrides_yaml(config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("RISK_MAX_TRADE", "3.5")
    monkeypatch.setenv("TRADING_MODE", "paper")
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    assert settings.get("risk.max_trade") == pytest.approx(3.5)
    assert settings.mode == "paper"


def test_invalid_env_number_raises(config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("RISK_MAX_TRADE", "not-a-number")
    with pytest.raises(ConfigError):
        load_settings(config_path=config_path, env_file=tmp_path / "absent.env")


def test_dotted_get_with_default(config_path, tmp_path):
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    assert settings.get("does.not.exist", "fallback") == "fallback"


def test_secrets_are_read_from_env_only(config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    assert settings.secrets.has_telegram()


def test_secrets_never_serialise(config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "cd" * 32)
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    assert "cd" * 32 not in repr(settings.secrets)
    assert "cd" * 32 not in str(settings.redacted_summary())


def test_redacted_summary_shape(config_path, tmp_path):
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    payload = settings.redacted_summary()
    assert payload["auth"]["polymarket_private_key"] == "missing"
    assert "risk" in payload and "strategy" in payload


def test_live_mode_without_flag_is_a_config_problem(config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.delenv("POLYMARKET_ALLOW_LIVE_TRADING", raising=False)
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    problems = settings.validate()
    assert any("POLYMARKET_ALLOW_LIVE_TRADING" in p for p in problems)


def test_invalid_mode_rejected(config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "yolo")
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    assert any("TRADING_MODE" in p for p in settings.validate())


def test_inconsistent_risk_limits_are_reported(config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("RISK_MAX_TRADE", "100000")
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    assert any("max_trade" in p for p in settings.validate())


def test_bad_kelly_fraction_is_reported(config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("RISK_KELLY_FRACTION", "5")
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    assert any("kelly" in p for p in settings.validate())


def test_ensure_dirs_creates_paths(settings):
    settings.ensure_dirs()
    assert (settings.repo_root / "data").exists()
    assert (settings.repo_root / "logs").exists()


def test_abs_path_resolves_relative(settings):
    assert settings.abs_path("data").is_absolute()


def test_secrets_helper_flags():
    assert Secrets(polymarket_private_key="x").has_polymarket_credentials()
    assert Secrets(api_key="a", api_secret="b", api_passphrase="c").has_polymarket_api_creds()
    assert not Secrets(api_key="a").has_polymarket_api_creds()


def test_default_live_confirmation_phrase(config_path, tmp_path):
    settings = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    assert settings.live_confirmation_phrase == "I UNDERSTAND THE RISK"
