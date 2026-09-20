"""Provider abstraction.

Every external data source implements :class:`Provider`. The engine only ever
talks to this interface, so swapping or adding a provider (paid or free) never
touches the prediction code.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.core.http import HttpClient, HttpError
from src.monitoring.health import Health, HealthRegistry


@dataclass
class ProviderStats:
    requests: int = 0
    failures: int = 0
    last_error: str = ""
    last_success_ts: float = 0.0
    total_ms: float = 0.0

    @property
    def error_rate(self) -> float:
        return self.failures / self.requests if self.requests else 0.0

    def as_dict(self) -> dict:
        return {
            "requests": self.requests,
            "failures": self.failures,
            "error_rate": round(self.error_rate, 3),
            "avg_ms": round(self.total_ms / self.requests, 1) if self.requests else 0.0,
            "last_error": self.last_error,
        }


class ProviderError(RuntimeError):
    """A provider could not deliver usable data (never silently swallowed)."""


@dataclass
class Provider(ABC):
    """Base class for all data providers."""

    name: str = "provider"
    kind: str = "generic"
    client: HttpClient | None = None
    enabled: bool = True
    timeout_seconds: float = 30.0
    stats: ProviderStats = field(default_factory=ProviderStats)
    health: HealthRegistry | None = None

    @abstractmethod
    def health_check(self) -> Health:
        """Cheap liveness probe used by pre-flight and the dashboard."""

    # ---------------------------------------------------------------- helpers
    def _fetch_json(self, url: str, params: dict | None = None) -> Any:
        if self.client is None:
            raise ProviderError(f"{self.name}: no HTTP client configured")
        started = time.perf_counter()
        self.stats.requests += 1
        try:
            data = self.client.get_json(url, params)
        except HttpError as exc:
            self.stats.failures += 1
            self.stats.last_error = str(exc)
            self._mark_health(Health.CRITICAL, str(exc))
            raise ProviderError(f"{self.name}: {exc}") from exc
        except Exception as exc:  # defensive: unknown transport failure
            self.stats.failures += 1
            self.stats.last_error = str(exc)
            self._mark_health(Health.CRITICAL, str(exc))
            raise ProviderError(f"{self.name}: unexpected error: {exc}") from exc
        finally:
            self.stats.total_ms += (time.perf_counter() - started) * 1000
        self.stats.last_success_ts = time.time()
        return data

    def _fetch_text(self, url: str, params: dict | None = None) -> str:
        if self.client is None:
            raise ProviderError(f"{self.name}: no HTTP client configured")
        started = time.perf_counter()
        self.stats.requests += 1
        try:
            text = self.client.get_text(url, params)
        except HttpError as exc:
            self.stats.failures += 1
            self.stats.last_error = str(exc)
            self._mark_health(Health.CRITICAL, str(exc))
            raise ProviderError(f"{self.name}: {exc}") from exc
        finally:
            self.stats.total_ms += (time.perf_counter() - started) * 1000
        self.stats.last_success_ts = time.time()
        return text

    def _mark_health(self, state: Health, detail: str = "") -> None:
        if self.health is not None:
            self.health.record(self.name, state, detail)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "enabled": self.enabled,
            "stats": self.stats.as_dict(),
        }
