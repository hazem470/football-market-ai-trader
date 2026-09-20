"""CLI surface: parsing, safety gates and read-only commands."""
from __future__ import annotations

import pytest

from app.cli.main import build_parser, cmd_lint_config, cmd_version, main

REPO_CONFIG = __import__("pathlib").Path(__file__).resolve().parents[1] / "configs" / "config.yaml"


def test_version_command(capsys):
    assert cmd_version(None) == 0
    out = capsys.readouterr().out
    assert "football-market-ai-trader" in out
    assert "disabled by default" in out


def test_parser_requires_a_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_accepts_every_documented_command():
    parser = build_parser()
    for command in ("init", "check", "config", "markets", "signals", "backtest", "calibrate",
                    "shadow", "paper", "live", "status", "stop", "test", "lint-config"):
        args = parser.parse_args([command])
        assert args.command == command


def test_live_requires_the_risk_flag_and_phrase(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("trading:\n  mode: backtest\n", encoding="utf-8")
    code = main(["--config", str(config), "live"])
    assert code == 3
    assert "Refusing to start" in capsys.readouterr().err


def test_live_rejects_a_wrong_confirmation_phrase(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("POLYMARKET_ALLOW_LIVE_TRADING", "true")
    config = tmp_path / "config.yaml"
    config.write_text("trading:\n  mode: backtest\n  live_trading: true\n", encoding="utf-8")
    code = main(["--config", str(config), "live", "--i-understand-risk", "--confirm", "yes"])
    assert code == 3
    assert "must be exactly" in capsys.readouterr().err


def test_live_rejects_when_the_env_flag_is_missing(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("POLYMARKET_ALLOW_LIVE_TRADING", raising=False)
    config = tmp_path / "config.yaml"
    config.write_text("trading:\n  mode: backtest\n  live_trading: false\n", encoding="utf-8")
    code = main(["--config", str(config), "live", "--i-understand-risk",
                 "--confirm", "I UNDERSTAND THE RISK"])
    assert code == 3
    assert "POLYMARKET_ALLOW_LIVE_TRADING" in capsys.readouterr().err


def test_lint_config_passes_on_the_shipped_config(config_path, monkeypatch, capsys):
    monkeypatch.setattr("app.cli.main.load_settings", lambda **kwargs: __import__(
        "src.config.settings", fromlist=["load_settings"]
    ).load_settings(config_path=config_path, env_file=None))
    assert cmd_lint_config(None) == 0
    assert "valid" in capsys.readouterr().out


def test_config_command_redacts_secrets(config_path, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "ef" * 32)
    code = main(["--config", str(config_path), "--json", "config"])
    out = capsys.readouterr().out
    assert code in (0, 2)
    assert "ef" * 32 not in out
    assert "polymarket_private_key" in out


def test_stop_command_engages_the_kill_switch(settings, tmp_path, capsys):
    from src.core.runtime import Runtime

    runtime = Runtime.build(settings, load_data=False, build_venue=True)
    try:
        runtime.stop("unit test")
        assert runtime.risk.breaker.manual_stop
        assert not runtime.risk.breaker.allows_new_orders()[0]
    finally:
        runtime.close()


def test_league_pool_lookup_is_case_insensitive(tmp_path, monkeypatch):
    """`--league E0` must select E0, not silently fall back to every league."""
    from src.config.settings import load_settings
    from src.core.runtime import Runtime
    from tests.conftest import make_match

    resolved = load_settings(config_path=REPO_CONFIG, env_file=tmp_path / "absent.env")
    resolved.repo_root = tmp_path
    resolved.database_path = str(tmp_path / "t.db")
    resolved.ensure_dirs()
    runtime = Runtime.build(resolved, load_data=False, build_venue=False)
    try:
        from src.data.normalization.canonical import TeamHistory

        runtime.history = TeamHistory([
            make_match("A", "B", 1, 0, league="E0"),
            make_match("C", "D", 1, 0, league="D1"),
        ])
        assert len(runtime._resolve_pool("E0")) == 1
        assert len(runtime._resolve_pool("e0")) == 1
        assert len(runtime._resolve_pool("D1")) == 1
        assert len(runtime._resolve_pool("")) == 2
        assert runtime._resolve_pool("NOPE") == []
    finally:
        runtime.close()


def test_backtest_reports_an_error_for_an_unknown_league(tmp_path):
    from src.config.settings import load_settings
    from src.core.runtime import Runtime

    resolved = load_settings(config_path=REPO_CONFIG, env_file=tmp_path / "absent.env")
    resolved.repo_root = tmp_path
    resolved.database_path = str(tmp_path / "t.db")
    resolved.ensure_dirs()
    runtime = Runtime.build(resolved, load_data=False, build_venue=False)
    try:
        from src.data.normalization.canonical import TeamHistory
        from tests.conftest import make_match

        runtime.history = TeamHistory([make_match("A", "B", 1, 0, league="E0")])
        report = runtime.run_backtest(market_type="MATCH_RESULT", league="ZZZ")
        assert "error" in report
        assert "ZZZ" in report["error"] or "no finished matches" in report["error"]
    finally:
        runtime.close()
