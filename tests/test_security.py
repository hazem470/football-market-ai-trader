"""Security: secrets must never leak, live trading must never be reachable by accident."""
from __future__ import annotations

import json
import logging

import pytest

from src.config.settings import load_settings
from src.execution.polymarket import LiveExecutionDisabled, PolymarketExecutor
from src.monitoring.logging_setup import redact_mapping, redact_text, setup_logging
from src.wallet.keystore import (
    SeedPhraseRejected,
    WalletConfig,
    assert_not_a_mnemonic,
    load_signer,
    validate_private_key,
    wallet_setup_instructions,
)

FAKE_KEY = "0x" + "ab" * 32
FAKE_MNEMONIC = " ".join(["abandon"] * 12)
# Built at runtime so no literal bot-token pattern exists in the source of this
# file (otherwise the repository secret scanner would flag its own test).
FAKE_BOT_TOKEN = "123456789" + ":" + "AAFakeTelegramBotTokenValueHere123456"


def test_redact_text_masks_a_private_key():
    assert FAKE_KEY not in redact_text(f"key={FAKE_KEY}")
    assert "[REDACTED]" in redact_text(f"key={FAKE_KEY}")


def test_redact_text_masks_a_mnemonic():
    assert "abandon" not in redact_text(f"seed: {FAKE_MNEMONIC}")


def test_redact_text_masks_a_telegram_token():
    assert FAKE_BOT_TOKEN not in redact_text(f"token {FAKE_BOT_TOKEN}")


def test_redact_text_masks_bearer_and_api_key_pairs():
    assert "secretvalue123" not in redact_text("Authorization: Bearer secretvalue123")
    assert "secretvalue123" not in redact_text("api_key=secretvalue123")


def test_redact_mapping_masks_sensitive_keys():
    payload = redact_mapping({"private_key": FAKE_KEY, "api_secret": "x", "market_id": "M1"})
    assert payload["private_key"] == "[REDACTED]"
    assert payload["api_secret"] == "[REDACTED]"
    assert payload["market_id"] == "M1"


def test_redact_mapping_is_recursive():
    payload = redact_mapping({"outer": {"bot_token": FAKE_BOT_TOKEN, "keep": "yes"}})
    assert payload["outer"]["bot_token"] == "[REDACTED]"
    assert payload["outer"]["keep"] == "yes"


def test_logging_filter_redacts_every_handler(tmp_path, capsys):
    logger = setup_logging("INFO", tmp_path, as_json=False, component="security_test", force=True)
    logger.warning("leaking %s", FAKE_KEY)
    captured = capsys.readouterr()
    written = (tmp_path / "football_trader.log").read_text(encoding="utf-8")
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in written


def test_json_logging_redacts_too(tmp_path):
    logger = logging.getLogger("json_redaction_test")
    logger.handlers.clear()
    handler = logging.FileHandler(tmp_path / "json.log", encoding="utf-8")
    from src.monitoring.logging_setup import JsonFormatter, RedactingFilter

    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("credential %s", FAKE_KEY)
    handler.close()
    assert FAKE_KEY not in (tmp_path / "json.log").read_text(encoding="utf-8")


def test_seed_phrase_is_rejected():
    with pytest.raises(SeedPhraseRejected):
        assert_not_a_mnemonic(FAKE_MNEMONIC)


def test_private_key_validation_rejects_nonsense():
    from src.wallet.keystore import WalletError

    with pytest.raises(WalletError):
        validate_private_key("not-a-key")


def test_private_key_validation_accepts_hex():
    assert validate_private_key(FAKE_KEY) == FAKE_KEY
    assert validate_private_key("ab" * 32) == "ab" * 32


def test_private_key_validation_rejects_a_mnemonic():
    with pytest.raises(SeedPhraseRejected):
        validate_private_key(FAKE_MNEMONIC)


def test_wallet_config_repr_never_leaks_the_key():
    wallet = WalletConfig(private_key=FAKE_KEY, funder_address="0x1234")
    text = repr(wallet) + str(wallet)
    assert FAKE_KEY not in text
    assert "set" in text


def test_wallet_config_redacted_summary():
    payload = WalletConfig(private_key=FAKE_KEY).redacted()
    assert payload["private_key"] == "set"
    assert FAKE_KEY not in json.dumps(payload)


def test_load_signer_reports_source(monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", FAKE_KEY)
    key, source = load_signer(allow_keyring=False)
    assert key == FAKE_KEY and source == "env"


def test_load_signer_absent(monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    key, source = load_signer(allow_keyring=False)
    assert key == "" and source == "none"


def test_load_signer_rejects_a_mnemonic_in_env(monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", FAKE_MNEMONIC)
    with pytest.raises(SeedPhraseRejected):
        load_signer(allow_keyring=False)


def test_wallet_instructions_never_mention_pasting_a_seed_phrase_as_a_steps():
    text = wallet_setup_instructions()
    assert "NEVER paste a seed phrase" in text
    assert "DEDICATED" in text


def test_settings_repr_hides_secrets(settings):
    for name in ("polymarket_private_key", "api_secret", "bot_token"):
        setattr(settings.secrets, name, "supersecretvalue")
    text = repr(settings.secrets)
    assert "supersecretvalue" not in text


def test_live_executor_refuses_without_the_gate():
    executor = PolymarketExecutor(wallet=WalletConfig(private_key=FAKE_KEY), allow_live=False)
    with pytest.raises(LiveExecutionDisabled):
        executor.initialise()


def test_live_executor_preflight_reports_the_gate():
    executor = PolymarketExecutor(wallet=WalletConfig(), allow_live=False)
    ok, problems = executor.preflight()
    assert not ok
    assert any("DISABLED" in problem for problem in problems)


def test_live_executor_requires_a_signer():
    executor = PolymarketExecutor(wallet=WalletConfig(), allow_live=True)
    ok, problems = executor.preflight()
    assert not ok
    assert any("seed phrase" in problem or "private" in problem.lower() for problem in problems)


def test_live_settings_require_a_key(settings, monkeypatch):
    monkeypatch.setenv("POLYMARKET_ALLOW_LIVE_TRADING", "true")
    resolved = load_settings(config_path=settings.config_path, env_file=settings.repo_root / "none.env")
    resolved.mode = "live"
    resolved.live_trading = True
    resolved.config["trading"]["mode"] = "live"
    problems = resolved.validate()
    assert any("POLYMARKET_PRIVATE_KEY" in problem for problem in problems)


def test_gitignore_covers_secrets():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "*.db", "logs/", ".venv/", "secrets/", "credentials/"):
        assert pattern in text


@pytest.mark.parametrize("needle", [
    "_KEY", "_SECRET", "PASSPHRASE", "BOT_TOKEN", "PRIVATE_KEY", "PASSWORD",
])
def test_env_example_ships_no_credential_values(needle):
    """Every credential-bearing variable must ship EMPTY in the template."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    checked = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if needle not in key.upper():
            continue
        checked += 1
        assert value.strip() == "", f"{key} must be empty in .env.example, found {value!r}"
    assert checked > 0, f"no variable matched {needle}; the template lost its placeholders"


def test_env_example_declares_live_off_by_default():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "POLYMARKET_ALLOW_LIVE_TRADING=false" in text
    assert "TRADING_MODE=backtest" in text


def test_repo_secret_scanner_runs_clean():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "scan_secrets.py")],
        capture_output=True, text=True, cwd=str(root),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_gitignore_covers_sqlite_sidecar_files():
    """`*.db` alone does NOT match the -wal / -shm files SQLite creates, and those
    files contain live database pages."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("*.db", "*.db-wal", "*.db-shm", "*.sqlite", "data/*.db*"):
        assert pattern in text, f".gitignore lost the {pattern} pattern"


def test_no_runtime_artifacts_are_tracked():
    """Guard: databases, WAL sidecars, logs and virtualenvs must never be tracked."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    try:
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                             cwd=str(root), check=True).stdout
    except Exception:
        pytest.skip("not a git checkout")
    offenders = [
        line for line in out.splitlines()
        if line.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"))
        or line.startswith(("logs/", "data/", "artifacts/", ".venv/", "venv/"))
    ]
    assert offenders == [], f"runtime artifacts tracked: {offenders}"
