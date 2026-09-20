"""Provider registry: constructs and health-checks the configured data stack."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.config.settings import Settings
from src.core.http import HttpClient, HttpxClient
from src.data.providers.base import Provider
from src.data.providers.football_data_uk import FootballDataUKProvider
from src.data.providers.fpl import FplProvider
from src.data.providers.openfootball import OpenFootballProvider
from src.data.providers.polymarket import ClobProvider, GammaProvider
from src.monitoring.health import Health, HealthRegistry


@dataclass
class ProviderRegistry:
    settings: Settings
    client: HttpClient | None = None
    health: HealthRegistry = field(default_factory=HealthRegistry)
    providers: dict[str, Provider] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = HttpxClient(timeout_seconds=30.0)
        cfg = self.settings.get("data.providers", {}) or {}
        gamma_cfg = cfg.get("polymarket_gamma", {}) or {}
        clob_cfg = cfg.get("polymarket_clob", {}) or {}
        fduk_cfg = cfg.get("football_data_uk", {}) or {}
        fpl_cfg = cfg.get("fpl", {}) or {}
        of_cfg = cfg.get("openfootball", {}) or {}

        self.providers = {
            "polymarket_gamma": GammaProvider(
                client=self.client,
                health=self.health,
                enabled=bool(gamma_cfg.get("enabled", True)),
                host=self.settings.gamma_host,
                page_limit=int(self.settings.get("markets.page_limit", 100)),
                max_pages=int(self.settings.get("markets.max_pages", 10)),
            ),
            "polymarket_clob": ClobProvider(
                client=self.client,
                health=self.health,
                enabled=bool(clob_cfg.get("enabled", True)),
                host=self.settings.clob_host,
            ),
            "football_data_uk": FootballDataUKProvider(
                client=self.client,
                health=self.health,
                enabled=bool(fduk_cfg.get("enabled", True)),
                base_url=str(fduk_cfg.get("base_url", "https://www.football-data.co.uk")),
                season=str(fduk_cfg.get("season", "2526")),
                leagues=tuple(fduk_cfg.get("leagues", ["E0"])),
            ),
            "fpl": FplProvider(
                client=self.client,
                health=self.health,
                enabled=bool(fpl_cfg.get("enabled", True)),
                base_url=str(fpl_cfg.get("base_url", "https://fantasy.premierleague.com/api")),
            ),
            "openfootball": OpenFootballProvider(
                client=self.client,
                health=self.health,
                enabled=bool(of_cfg.get("enabled", True)),
                base_url=str(of_cfg.get("base_url",
                    "https://raw.githubusercontent.com/openfootball/football.json/master")),
            ),
        }

    def get(self, name: str) -> Provider:
        provider = self.providers.get(name)
        if provider is None:
            raise KeyError(f"unknown provider: {name}")
        return provider

    @property
    def gamma(self) -> GammaProvider:
        return self.get("polymarket_gamma")  # type: ignore[return-value]

    @property
    def clob(self) -> ClobProvider:
        return self.get("polymarket_clob")  # type: ignore[return-value]

    @property
    def football_data_uk(self) -> FootballDataUKProvider:
        return self.get("football_data_uk")  # type: ignore[return-value]

    @property
    def fpl(self) -> FplProvider:
        return self.get("fpl")  # type: ignore[return-value]

    @property
    def openfootball(self) -> OpenFootballProvider:
        return self.get("openfootball")  # type: ignore[return-value]

    def enabled_providers(self) -> list[Provider]:
        return [p for p in self.providers.values() if p.enabled]

    def check_all(self, only: list[str] | None = None) -> dict[str, str]:
        results: dict[str, str] = {}
        for name, provider in self.providers.items():
            if only and name not in only:
                continue
            if not provider.enabled:
                results[name] = "DISABLED"
                self.health.record(name, Health.HEALTHY, "disabled by configuration")
                continue
            try:
                results[name] = provider.health_check().value
            except Exception as exc:  # never let a probe crash pre-flight
                self.health.record(name, Health.CRITICAL, f"probe failed: {exc}")
                results[name] = "CRITICAL"
        return results

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
