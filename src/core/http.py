"""Thin HTTP abstraction so tests never touch the network.

Every network call in this project goes through an :class:`HttpClient`.
Tests inject a fake; the CLI injects :class:`HttpxClient`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class HttpError(RuntimeError):
    """Raised for transport errors and non-2xx responses."""

    def __init__(self, message: str, status_code: int | None = None, url: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class RateLimited(HttpError):
    """429 from an upstream provider (retryable with backoff)."""


@dataclass
class HttpResponse:
    url: str
    status_code: int
    json_data: Any = None
    text: str = ""
    elapsed_ms: float = 0.0
    from_cache: bool = False


@runtime_checkable
class HttpClient(Protocol):
    def get_json(self, url: str, params: dict | None = None) -> Any: ...
    def get_text(self, url: str, params: dict | None = None) -> str: ...


@dataclass
class CallStats:
    calls: int = 0
    errors: int = 0
    rate_limited: int = 0
    total_ms: float = 0.0
    _latencies: list[float] = field(default_factory=list)

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else 0.0

    @property
    def p95_ms(self) -> float:
        if not self._latencies:
            return 0.0
        ordered = sorted(self._latencies)
        idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[idx]

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "errors": self.errors,
            "rate_limited": self.rate_limited,
            "avg_ms": round(self.avg_ms, 1),
            "p95_ms": round(self.p95_ms, 1),
        }

    def record(self, elapsed_ms: float, ok: bool, rate_limited: bool = False) -> None:
        self.calls += 1
        self.total_ms += elapsed_ms
        self._latencies.append(elapsed_ms)
        if not ok:
            self.errors += 1
        if rate_limited:
            self.rate_limited += 1


class HttpxClient:
    """Real client. One shared connection pool, retries with exponential backoff.

    Retries apply to *read-only* GETs only - order submission uses a separate,
    non-retrying path (see src/execution/polymarket.py).
    """

    def __init__(
        self,
        timeout_seconds: float = 20.0,
        user_agent: str = "football-market-ai-trader/1.0 (+open-source research bot)",
        headers: dict | None = None,
    ) -> None:
        import httpx  # imported lazily so the package is optional for pure-logic use

        self._httpx = httpx
        default_headers = {"User-Agent": user_agent, "Accept": "application/json"}
        if headers:
            default_headers.update(headers)
        self._client = httpx.Client(timeout=timeout_seconds, headers=default_headers, follow_redirects=True)
        self.stats = CallStats()

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type((RateLimited,)),
    )
    def _get(self, url: str, params: dict | None) -> Any:
        started = time.perf_counter()
        try:
            response = self._client.get(url, params=params)
        except Exception as exc:  # transport failure
            self.stats.record((time.perf_counter() - started) * 1000, ok=False)
            raise HttpError(f"transport error: {exc}", url=url) from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code == 429:
            self.stats.record(elapsed_ms, ok=False, rate_limited=True)
            raise RateLimited("rate limited", status_code=429, url=url)
        if response.status_code >= 400:
            self.stats.record(elapsed_ms, ok=False)
            raise HttpError(
                f"HTTP {response.status_code} for {url}", status_code=response.status_code, url=url
            )
        self.stats.record(elapsed_ms, ok=True)
        return response

    def get_json(self, url: str, params: dict | None = None) -> Any:
        response = self._get(url, params)
        try:
            return response.json()
        except Exception as exc:
            raise HttpError(f"invalid JSON from {url}: {exc}", url=url) from exc

    def get_text(self, url: str, params: dict | None = None) -> str:
        return self._get(url, params).text

    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> Any:
        """Non-retrying POST. Used for notifications and order submission."""
        started = time.perf_counter()
        try:
            response = self._client.post(url, json=payload, headers=headers or {})
        except Exception as exc:
            self.stats.record((time.perf_counter() - started) * 1000, ok=False)
            raise HttpError(f"transport error: {exc}", url=url) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.stats.record(elapsed_ms, ok=response.status_code < 400)
        if response.status_code >= 400:
            raise HttpError(
                f"HTTP {response.status_code} for {url}: {response.text[:200]}",
                status_code=response.status_code,
                url=url,
            )
        return response.json()

    def close(self) -> None:
        self._client.close()


class FakeHttpClient:
    """Deterministic in-memory client for tests and offline demos.

    ``routes`` maps a URL (or URL prefix) to either a JSON payload, a callable
    taking the params dict, or an Exception instance to raise.
    """

    def __init__(self, routes: dict | None = None) -> None:
        self.routes: dict = dict(routes or {})
        self.calls: list[tuple[str, dict | None]] = []
        self.stats = CallStats()

    def _resolve(self, url: str, params: dict | None) -> Any:
        self.calls.append((url, params))
        candidate = self.routes.get(url)
        if candidate is None:
            for prefix, value in self.routes.items():
                if url.startswith(prefix):
                    candidate = value
                    break
        if candidate is None:
            self.stats.record(0.0, ok=False)
            raise HttpError(f"no fake route for {url}", status_code=404, url=url)
        if isinstance(candidate, Exception):
            self.stats.record(0.0, ok=False)
            raise candidate
        if callable(candidate):
            candidate = candidate(params)
        self.stats.record(0.0, ok=True)
        return candidate

    def get_json(self, url: str, params: dict | None = None) -> Any:
        return self._resolve(url, params)

    def get_text(self, url: str, params: dict | None = None) -> str:
        value = self._resolve(url, params)
        return value if isinstance(value, str) else str(value)

    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> Any:
        return self._resolve(url, payload)

    def close(self) -> None:  # pragma: no cover
        pass
