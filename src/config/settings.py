"""Configuration loader.

Precedence (highest first):
  1. Environment variables (real environment, then `.env`)
  2. `configs/config.yaml`
  3. Built-in defaults in `configs/config.yaml` shipped with the repo

Secrets are NEVER read from YAML - only from the environment/.env, and they are
never written back, logged, or serialised.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:  # python-dotenv is a declared dependency, but keep the import defensive
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs) -> bool:  # type: ignore[misc]
        return False

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "config.yaml"

VALID_MODES = ("backtest", "shadow", "paper", "live")
LIVE_CONFIRMATION_DEFAULT = "I UNDERSTAND THE RISK"


class ConfigError(RuntimeError):
    """Raised when configuration is missing, malformed or unsafe."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(float(raw))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return default if raw is None else raw.strip()


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(repr=False)
class Secrets:
    """Credential container.

    ``repr=False`` is essential: a dataclass-generated ``__repr__`` would list
    every field value, which would print a private key into any traceback or log
    line. The explicit ``__repr__`` below reports presence only.
    """

    polymarket_private_key: str = ""
    # `api_key` / `api_secret` / `api_passphrase` are shorthand accepted by the
    # contract tests and by embedders; they mirror the POLYMARKET_* names.
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    polymarket_funder_address: str = ""
    ai_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def polymarket_api_key(self) -> str:
        return self.api_key

    @property
    def polymarket_api_secret(self) -> str:
        return self.api_secret

    @property
    def polymarket_api_passphrase(self) -> str:
        return self.api_passphrase

    def __repr__(self) -> str:  # pragma: no cover - defensive
        present = [
            name
            for name, value in self.__dict__.items()
            if value
        ]
        return f"Secrets(configured={present})"

    __str__ = __repr__

    def has_polymarket_credentials(self) -> bool:
        return bool(self.polymarket_private_key)

    def has_polymarket_api_creds(self) -> bool:
        return bool(
            self.polymarket_api_key
            and self.polymarket_api_secret
            and self.polymarket_api_passphrase
        )

    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def has_ai(self) -> bool:
        return bool(self.ai_api_key)


@dataclass
class Settings:
    """Fully resolved, validated runtime settings."""

    config: dict[str, Any] = field(default_factory=dict)
    secrets: Secrets = field(default_factory=Secrets)
    repo_root: Path = REPO_ROOT
    config_path: Path = DEFAULT_CONFIG_PATH
    env_file: Path | None = None
    mode: str = "backtest"
    live_trading: bool = False
    live_confirmation_phrase: str = LIVE_CONFIRMATION_DEFAULT
    log_level: str = "INFO"
    log_json: bool = False
    log_dir: str = "logs"
    database_path: str = "data/football_trader.db"
    data_dir: str = "data"
    cache_dir: str = "data/cache"
    gamma_host: str = "https://gamma-api.polymarket.com"
    clob_host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    signature_type: int = 0
    timezone: str = "UTC"

    # ---- helpers -------------------------------------------------------
    def section(self, name: str) -> dict:
        value = self.config.get(name)
        return value if isinstance(value, dict) else {}

    def get(self, path: str, default: Any = None) -> Any:
        """Dotted lookup: ``settings.get("risk.max_trade", 10)``."""
        node: Any = self.config
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_simulated(self) -> bool:
        return self.mode in ("backtest", "shadow", "paper")

    def abs_path(self, relative: str | Path) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else (self.repo_root / path)

    def ensure_dirs(self) -> None:
        for raw in (self.data_dir, self.cache_dir, self.log_dir, str(Path(self.database_path).parent)):
            if not raw or raw == ".":
                continue
            self.abs_path(raw).mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        """Return a list of hard configuration problems (empty == valid)."""
        problems: list[str] = []
        if self.mode not in VALID_MODES:
            problems.append(f"TRADING_MODE must be one of {VALID_MODES}, got {self.mode!r}")
        if self.mode == "live" and not self.live_trading:
            problems.append(
                "TRADING_MODE=live requires POLYMARKET_ALLOW_LIVE_TRADING=true "
                "(live trading is disabled by default)"
            )
        risk = self.section("risk")
        capital = float(risk.get("capital", 0) or 0)
        max_trade = float(risk.get("max_trade", 0) or 0)
        daily_exp = float(risk.get("max_daily_exposure", 0) or 0)
        daily_loss = float(risk.get("max_daily_loss", 0) or 0)
        if capital <= 0:
            problems.append("risk.capital must be > 0")
        if max_trade <= 0:
            problems.append("risk.max_trade must be > 0")
        if max_trade > capital:
            problems.append("risk.max_trade cannot exceed risk.capital")
        if daily_exp > capital:
            problems.append("risk.max_daily_exposure cannot exceed risk.capital")
        if daily_loss > capital:
            problems.append("risk.max_daily_loss cannot exceed risk.capital")
        kelly = float(risk.get("kelly_fraction", 0.25) or 0.25)
        if kelly <= 0 or kelly > 1:
            problems.append("risk.kelly_fraction must be in (0, 1]")
        logic = self.section("strategy")
        min_edge = float(logic.get("min_edge", 0.08) or 0.08)
        if not (0 <= min_edge < 1):
            problems.append("strategy.min_edge must be in [0, 1)")
        min_conf = float(logic.get("min_confidence", 0.75) or 0.75)
        if not (0 <= min_conf <= 1):
            problems.append("strategy.min_confidence must be in [0, 1]")
        if self.mode == "live":
            if not self.secrets.has_polymarket_credentials():
                problems.append("live mode requires POLYMARKET_PRIVATE_KEY (dedicated trading wallet)")
            if self.chain_id not in (137, 80002):
                problems.append(f"POLYMARKET_CHAIN_ID must be 137 (Polygon) or 80002 (Amoy), got {self.chain_id}")
        return problems

    def redacted_summary(self) -> dict:
        """Safe-to-print summary used by `python -m app.cli config`."""
        return {
            "mode": self.mode,
            "live_trading": self.live_trading,
            "auth": {
                "polymarket_private_key": "set" if self.secrets.polymarket_private_key else "missing",
                "polymarket_api_creds": "set" if self.secrets.has_polymarket_api_creds() else "missing",
                "funder_address": "set" if self.secrets.polymarket_funder_address else "missing",
                "ai_api_key": "set" if self.secrets.has_ai() else "missing",
                "telegram": "set" if self.secrets.has_telegram() else "missing",
            },
            "gamma_host": self.gamma_host,
            "clob_host": self.clob_host,
            "chain_id": self.chain_id,
            "signature_type": self.signature_type,
            "risk": self.section("risk"),
            "strategy": self.section("strategy"),
            "database_path": self.database_path,
            "config_path": str(self.config_path),
        }


def load_settings(
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """Load, merge and validate configuration."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw_config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw_config, dict):
        raise ConfigError(f"config root must be a mapping in {path}")

    # .env resolution: explicit > repo root > cwd
    resolved_env: Path | None = None
    candidates = [Path(env_file)] if env_file else [REPO_ROOT / ".env", Path.cwd() / ".env"]
    for candidate in candidates:
        if candidate and candidate.exists():
            load_dotenv(candidate, override=False)
            resolved_env = candidate
            break

    config = raw_config

    # ---- environment overrides for non-secret, operational values -------
    trading = config.setdefault("trading", {})
    trading["mode"] = _env_str("TRADING_MODE", str(trading.get("mode", "backtest"))).lower()
    trading["live_trading"] = _env_bool("POLYMARKET_ALLOW_LIVE_TRADING", bool(trading.get("live_trading", False)))

    risk = config.setdefault("risk", {})
    risk["capital"] = _env_float("RISK_CAPITAL", float(risk.get("capital", 500.0)))
    risk["max_trade"] = _env_float("RISK_MAX_TRADE", float(risk.get("max_trade", 10.0)))
    risk["max_daily_exposure"] = _env_float("RISK_MAX_DAILY_EXPOSURE", float(risk.get("max_daily_exposure", 50.0)))
    risk["max_daily_loss"] = _env_float("RISK_MAX_DAILY_LOSS", float(risk.get("max_daily_loss", 20.0)))
    risk["max_open_positions"] = _env_int("RISK_MAX_OPEN_POSITIONS", int(risk.get("max_open_positions", 6)))
    risk["min_liquidity"] = _env_float("RISK_MIN_LIQUIDITY", float(risk.get("min_liquidity", 100.0)))
    risk["max_spread"] = _env_float("RISK_MAX_SPREAD", float(risk.get("max_spread", 0.04)))
    risk["max_slippage"] = _env_float("RISK_MAX_SLIPPAGE", float(risk.get("max_slippage", 0.02)))
    risk["max_correlated_exposure"] = _env_float(
        "RISK_MAX_CORRELATED_EXPOSURE", float(risk.get("max_correlated_exposure", 25.0))
    )
    risk["kelly_fraction"] = _env_float("RISK_KELLY_FRACTION", float(risk.get("kelly_fraction", 0.25)))

    strategy = config.setdefault("strategy", {})
    strategy["min_edge"] = _env_float("RISK_MIN_EDGE", float(strategy.get("min_edge", 0.08)))
    strategy["min_confidence"] = _env_float("RISK_MIN_CONFIDENCE", float(strategy.get("min_confidence", 0.75)))

    data_cfg = config.setdefault("data", {})
    providers = data_cfg.setdefault("providers", {})
    fduk = providers.setdefault("football_data_uk", {})
    fduk["enabled"] = _env_bool("FOOTBALL_DATA_UK_ENABLED", bool(fduk.get("enabled", True)))
    fduk["base_url"] = _env_str("FOOTBALL_DATA_UK_BASE_URL", str(fduk.get("base_url", "https://www.football-data.co.uk")))
    fduk["season"] = _env_str("FOOTBALL_DATA_UK_SEASON", str(fduk.get("season", "2526")))
    fduk["leagues"] = _env_list("FOOTBALL_DATA_UK_LEAGUES", list(fduk.get("leagues", ["E0"])))
    fpl = providers.setdefault("fpl", {})
    fpl["enabled"] = _env_bool("FPL_ENABLED", bool(fpl.get("enabled", True)))
    openfootball = providers.setdefault("openfootball", {})
    openfootball["enabled"] = _env_bool("OPENFOOTBALL_ENABLED", bool(openfootball.get("enabled", True)))

    ai_cfg = config.setdefault("ai", {})
    ai_cfg["enabled"] = _env_bool("AI_ENABLED", bool(ai_cfg.get("enabled", False)))
    ai_cfg["provider"] = _env_str("AI_PROVIDER", str(ai_cfg.get("provider", "openrouter")))
    ai_cfg["model"] = _env_str("AI_MODEL", str(ai_cfg.get("model", "openai/gpt-4o-mini")))
    ai_cfg["base_url"] = _env_str("AI_BASE_URL", str(ai_cfg.get("base_url", "https://openrouter.ai/api/v1")))

    notif = config.setdefault("notifications", {})
    tg = notif.setdefault("telegram", {})
    tg["enabled"] = _env_bool("TELEGRAM_ENABLED", bool(tg.get("enabled", False)))
    tg["min_edge_to_notify"] = _env_float("TELEGRAM_MIN_EDGE_TO_NOTIFY", float(tg.get("min_edge_to_notify", 0.08)))

    logging_cfg = config.setdefault("logging", {})
    logging_cfg["level"] = _env_str("LOG_LEVEL", str(logging_cfg.get("level", "INFO")))
    logging_cfg["json"] = _env_bool("LOG_JSON", bool(logging_cfg.get("json", False)))
    logging_cfg["dir"] = _env_str("LOG_DIR", str(logging_cfg.get("dir", "logs")))

    if overrides:
        _deep_merge(config, dict(overrides))

    secrets = Secrets(
        polymarket_private_key=_env_str("POLYMARKET_PRIVATE_KEY"),
        api_key=_env_str("POLYMARKET_API_KEY"),
        api_secret=_env_str("POLYMARKET_API_SECRET"),
        api_passphrase=_env_str("POLYMARKET_API_PASSPHRASE"),
        polymarket_funder_address=_env_str("POLYMARKET_FUNDER_ADDRESS"),
        ai_api_key=_env_str("AI_API_KEY"),
        telegram_bot_token=_env_str("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env_str("TELEGRAM_CHAT_ID"),
    )

    settings = Settings(
        config=config,
        secrets=secrets,
        repo_root=REPO_ROOT,
        config_path=path,
        env_file=resolved_env,
        mode=str(trading.get("mode", "backtest")),
        live_trading=bool(trading.get("live_trading", False)),
        live_confirmation_phrase=_env_str("LIVE_CONFIRMATION_PHRASE", LIVE_CONFIRMATION_DEFAULT),
        log_level=str(logging_cfg.get("level", "INFO")),
        log_json=bool(logging_cfg.get("json", False)),
        log_dir=str(logging_cfg.get("dir", "logs")),
        database_path=_env_str("DATABASE_PATH", str(data_cfg.get("database_path", "data/football_trader.db"))),
        data_dir=_env_str("DATA_DIR", str(data_cfg.get("data_dir", "data"))),
        cache_dir=_env_str("CACHE_DIR", str(data_cfg.get("cache_dir", "data/cache"))),
        gamma_host=_env_str("POLYMARKET_GAMMA_HOST", "https://gamma-api.polymarket.com"),
        clob_host=_env_str("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com"),
        chain_id=_env_int("POLYMARKET_CHAIN_ID", 137),
        signature_type=_env_int("POLYMARKET_SIGNATURE_TYPE", 0),
        timezone=str(config.get("app", {}).get("timezone", "UTC")),
    )
    return settings


def _deep_merge(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
