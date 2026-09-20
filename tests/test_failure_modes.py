"""Failure tests: every provider/network/permission failure must degrade safely."""
from __future__ import annotations

import time

import pytest

from src.core.http import FakeHttpClient, HttpError, RateLimited
from src.data.providers.base import ProviderError
from src.data.providers.football_data_uk import FootballDataUKProvider
from src.data.providers.fpl import FplProvider
from src.data.providers.polymarket import ClobProvider, GammaProvider
from src.markets.discovery import MarketDiscovery
from src.monitoring.health import Health, HealthRegistry
from src.risk.circuit_breaker import BreakerConfig, CircuitBreaker
from src.strategy.edge import CostConfig, calculate_edge


def test_http_error_is_raised_for_500():
    client = FakeHttpClient({"https://x/y": HttpError("HTTP 500", status_code=500)})
    with pytest.raises(HttpError):
        client.get_json("https://x/y")


def test_rate_limit_is_distinguishable():
    client = FakeHttpClient({"https://x/y": RateLimited("429", status_code=429)})
    with pytest.raises(RateLimited):
        client.get_json("https://x/y")


def test_provider_wraps_http_errors():
    provider = GammaProvider(client=FakeHttpClient({"https://gamma-api.polymarket.com/sports": HttpError("boom")}))
    with pytest.raises(ProviderError):
        provider.list_sports()


def test_gamma_health_is_critical_when_unreachable():
    provider = GammaProvider(client=FakeHttpClient({"https://gamma-api.polymarket.com/sports": HttpError("down")}))
    assert provider.health_check() is Health.CRITICAL


def test_gamma_health_is_warning_on_empty_response():
    provider = GammaProvider(client=FakeHttpClient({"https://gamma-api.polymarket.com/sports": []}))
    assert provider.health_check() is Health.WARNING


def test_clob_health_is_critical_when_unreachable():
    provider = ClobProvider(client=FakeHttpClient({"https://clob.polymarket.com/sampling-markets": HttpError("down")}))
    assert provider.health_check() is Health.CRITICAL


def test_football_data_health_detects_a_bad_csv():
    provider = FootballDataUKProvider(client=FakeHttpClient({"https://www.football-data.co.uk": "garbage payload"}))
    assert provider.health_check() is Health.CRITICAL


def test_football_data_empty_csv_raises():
    provider = FootballDataUKProvider(client=FakeHttpClient({"https://www.football-data.co.uk": ""}))
    with pytest.raises(ProviderError):
        provider.fetch_league_csv("E0")


def test_football_data_skips_unparseable_rows():
    csv_text = (
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HS,AS,HST,AST,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A\n"
        "E0,15/08/2025,Arsenal,Chelsea,2,1,14,9,5,3,7,3,1,2,0,0,1.8,3.6,4.5\n"
        "E0,22/08/2025,Liverpool,,3,1,14,9,5,3,7,3,1,2,0,0,1.8,3.6,4.5\n"
        "E0,29/08/2025,Everton,Fulham,,,14,9,5,3,7,3,1,2,0,0,1.8,3.6,4.5\n"
    )
    provider = FootballDataUKProvider(client=FakeHttpClient({"https://www.football-data.co.uk": csv_text}))
    rows = provider.parse_league_csv(csv_text, "E0")
    assert len(rows) == 1
    assert rows[0]["home_goals"] == 2 and rows[0]["away_goals"] == 1
    assert rows[0]["match_date"] == "2025-08-15"


def test_football_data_one_bad_league_does_not_kill_the_load():
    client = FakeHttpClient({
        "https://www.football-data.co.uk/mmz4281/2526/E0.csv": HttpError("missing"),
        "https://www.football-data.co.uk/mmz4281/2526/D1.csv":
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nD1,15/08/2025,Bayern,Dortmund,2,1\n",
    })
    provider = FootballDataUKProvider(client=client, leagues=("E0", "D1"))
    rows = provider.fetch_all()
    assert len(rows) == 1


def test_football_data_all_leagues_missing_raises():
    client = FakeHttpClient({"https://www.football-data.co.uk": HttpError("down")})
    provider = FootballDataUKProvider(client=client, leagues=("E0",))
    with pytest.raises(ProviderError):
        provider.fetch_all()


def test_fpl_unexpected_payload_raises():
    provider = FplProvider(client=FakeHttpClient({
        "https://fantasy.premierleague.com/api/bootstrap-static/": {"unexpected": True},
    }))
    with pytest.raises(ProviderError):
        provider.bootstrap()


def test_discovery_survives_a_failing_book():
    gamma_client = FakeHttpClient({
        "https://gamma-api.polymarket.com/tags/slug/soccer": {"id": "100350"},
        "https://gamma-api.polymarket.com/events/keyset": {
            "events": [{
                "id": "1", "title": "Arsenal vs. Chelsea",
                "tags": [{"slug": "soccer", "label": "Soccer"}],
                "series": [{"title": "Premier League"}],
                "markets": [{
                    "id": "M1", "question": "Will Arsenal win?", "sportsMarketType": "moneyline",
                    "groupItemTitle": "Arsenal", "clobTokenIds": '["1","2"]',
                    "bestBid": 0.4, "bestAsk": 0.5, "liquidityNum": 1000.0,
                    "endDateIso": "2027-01-01T00:00:00Z",
                }],
            }],
            "next_cursor": None,
        },
    })
    clob_client = FakeHttpClient({"https://clob.polymarket.com/book": HttpError("book down")})
    discovery = MarketDiscovery(gamma=GammaProvider(client=gamma_client), clob=ClobProvider(client=clob_client))
    markets = discovery.discover()
    assert len(markets) == 1
    assert markets[0].snapshot.source == "gamma"
    assert discovery.stats.price_failed == 1


def test_missing_price_data_makes_the_edge_incomplete(market_factory):
    market = market_factory(bid=None, ask=None)
    market.snapshot.price = None
    market.snapshot.mid = None
    assert not calculate_edge(0.6, market, CostConfig()).is_complete


def test_circuit_breaker_trips_on_repeated_execution_failures():
    breaker = CircuitBreaker(config=BreakerConfig(max_consecutive_errors=3))
    for _ in range(3):
        breaker.record_error("execution failure")
    assert breaker.is_open


def test_market_with_a_future_resolution_far_out_is_refused(market_factory):
    from src.markets.filters import FilterConfig, passes_filters

    market = market_factory(end_time=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                  time.localtime(time.time() + 100 * 24 * 3600)))
    rejection = passes_filters(market, FilterConfig(min_hours_to_resolution=1, max_hours_to_resolution=240))
    assert rejection is not None


def test_health_registry_reports_the_worst_state():
    registry = HealthRegistry()
    registry.ok("a")
    registry.warn("b", "degraded")
    assert registry.overall() is Health.WARNING
    registry.critical("c", "down")
    assert registry.overall() is Health.CRITICAL
    allowed, reason = registry.trading_allowed()
    assert not allowed and "c" in reason


def test_health_registry_blocks_trading_on_critical_only():
    registry = HealthRegistry()
    registry.warn("a", "slow")
    assert registry.trading_allowed()[0] is True


def test_model_fit_failure_does_not_raise_in_the_registry(settings):
    from src.features.engine import FeatureEngine
    from src.markets.schema import MarketType
    from src.models.registry import ModelRegistry
    from tests.conftest import make_market

    registry = ModelRegistry(model_config={"match_result": {"min_history_matches": 100_000}})
    prediction = registry.predict(MarketType.MATCH_RESULT.value, "e0",
                                  FeatureEngine().build(make_market()), [])
    assert prediction.insufficient_data


def test_database_unavailable_is_detected(settings):
    import sqlite3
    from pathlib import Path

    from src.storage.database import Database

    # A directory where the database file should be makes sqlite fail on connect.
    blocked = Path(settings.database_path)
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.mkdir(exist_ok=True)
    with pytest.raises((sqlite3.OperationalError, OSError)):
        Database(str(blocked)).connect()


def test_invalid_credentials_are_reported_not_swallowed():
    from src.ai.openai_compatible import OpenAiCompatibleProvider

    provider = OpenAiCompatibleProvider(client=FakeHttpClient({}), api_key="")
    assert provider.available() is False


def test_ai_provider_rejects_payload_with_forbidden_fields():
    from src.ai.base import validate_extraction

    facts, rejected = validate_extraction({"facts": [], "decision": "BUY"})
    assert facts == []
    assert "forbidden" in rejected


def test_ai_extraction_accepts_only_well_formed_facts():
    from src.ai.base import validate_extraction

    facts, rejected = validate_extraction({
        "facts": [
            {"player": "Saka", "fact_type": "INJURY", "detail": "out", "confidence": 0.9},
            "not a dict",
        ]
    })
    assert rejected == ""
    assert len(facts) == 1 and facts[0].fact_type == "INJURY"
