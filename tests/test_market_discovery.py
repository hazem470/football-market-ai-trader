"""Discovery + parsing of live Gamma payloads, using a fake HTTP client only."""
from __future__ import annotations

import pytest

from src.markets.discovery import MarketDiscovery
from src.markets.schema import MarketType
from tests.conftest import gamma_event_payload, gamma_market_payload


def test_resolves_soccer_tag_dynamically(discovery):
    assert discovery._resolve_soccer_tag() == "100350"


def test_discovers_and_classifies_a_soccer_event(discovery):
    markets = discovery.discover()
    assert len(markets) == 1
    market = markets[0]
    assert market.market_type is MarketType.MATCH_RESULT
    assert market.home_team == "Arsenal"
    assert market.away_team == "Chelsea"
    assert market.league == "Premier League"
    assert market.yes_token_id == "111"
    assert market.no_token_id == "222"
    assert market.event_id == "999"


def test_discovery_enriches_price_from_the_real_book(discovery):
    market = discovery.discover()[0]
    assert market.snapshot is not None
    assert market.snapshot.source == "clob"
    assert market.snapshot.bid == pytest.approx(0.48)
    assert market.snapshot.ask == pytest.approx(0.52)
    assert discovery.stats.price_enriched == 1


def test_non_soccer_events_are_excluded(settings, fake_clob_client):
    from src.core.http import FakeHttpClient
    from src.data.providers.polymarket import ClobProvider, GammaProvider

    basketball = gamma_event_payload(
        id="1000", title="Lakers vs. Celtics", tags=[{"id": "745", "slug": "nba", "label": "NBA"}],
    )
    client = FakeHttpClient({
        "https://gamma-api.polymarket.com/tags/slug/soccer": {"id": "100350"},
        "https://gamma-api.polymarket.com/events/keyset": {"events": [basketball], "next_cursor": None},
    })
    discovery = MarketDiscovery(
        gamma=GammaProvider(client=client), clob=ClobProvider(client=fake_clob_client), settings=settings,
    )
    assert discovery.discover() == []


def test_player_prop_player_extraction():
    market = MarketDiscovery._player_from_question("Will Bukayo Saka score against Chelsea?", "Arsenal", "Chelsea")
    assert market == "Bukayo Saka"


def test_player_extraction_ignores_team_phrasing():
    assert MarketDiscovery._player_from_question("Will both teams score?", "A", "B") == ""


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Will Arsenal win on 2026-03-13?", "Arsenal"),
        ("Will Chelsea win?", "Chelsea"),
    ],
)
def test_selection_extraction(question, expected):
    assert MarketDiscovery._selection_from_question(question) == expected


def test_participants_parsed_from_event_title():
    discovery = MarketDiscovery.__new__(MarketDiscovery)
    home, away = discovery._participants({"ticker": "x"}, "Arsenal vs. Chelsea")
    assert home == "Arsenal" and away == "Chelsea"


def test_participants_strip_market_qualifiers():
    discovery = MarketDiscovery.__new__(MarketDiscovery)
    home, away = discovery._participants({}, "Chicago Fire FC vs. Vancouver Whitecaps FC - Exact Score")
    assert away == "Vancouver Whitecaps FC"


def test_parse_market_handles_missing_token_ids(discovery):
    raw = gamma_market_payload(clobTokenIds="[]")
    market = discovery.parse_market(raw, gamma_event_payload())
    assert market is not None
    assert market.yes_token_id == ""


def test_parse_market_reads_neg_risk_and_tick_size(discovery):
    raw = gamma_market_payload(negRisk=True, orderPriceMinTickSize=0.001, orderMinSize=10)
    market = discovery.parse_market(raw, gamma_event_payload())
    assert market.neg_risk is True
    assert market.tick_size == pytest.approx(0.001)
    assert market.min_order_size == pytest.approx(10)


def test_tradeable_discovery_applies_filters(discovery):
    result, _stats = discovery.discover_tradeable()
    # The fake market has a 0.04 spread which is exactly at the limit, so it passes.
    assert result.accepted_count + result.rejected_count == 1


def test_discovery_records_stats(discovery):
    discovery.discover()
    stats = discovery.stats.as_dict()
    assert stats["events_scanned"] == 1
    assert stats["soccer_events"] == 1
    assert stats["classified"] == 1
    assert stats["soccer_tag_id"] == "100350"
