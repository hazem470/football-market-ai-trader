"""Shared fixtures. No test in the default suite touches the network or needs credentials."""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest

from src.config.settings import Secrets, Settings, load_settings
from src.core.http import FakeHttpClient
from src.data.normalization.canonical import MatchRecord, Normalizer, PlayerRecord, TeamHistory
from src.markets.discovery import MarketDiscovery
from src.markets.schema import Market, MarketSnapshot, MarketType

REPO_ROOT = Path(__file__).resolve().parents[1]


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--run-network", action="store_true", default=False,
        help="also run tests marked 'network' (they hit public APIs)",
    )


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "network: test requires public network access")


def pytest_collection_modifyitems(config, items) -> None:
    if config.getoption("--run-network"):
        return
    skip = pytest.mark.skip(reason="network test; run with --run-network")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


# --------------------------------------------------------------------- settings
@pytest.fixture
def config_path() -> Path:
    return REPO_ROOT / "configs" / "config.yaml"


@pytest.fixture
def settings(config_path, tmp_path, monkeypatch) -> Settings:
    """Isolated settings: no .env pickup, temp paths, no credentials."""
    for name in (
        "POLYMARKET_PRIVATE_KEY", "POLYMARKET_API_KEY", "POLYMARKET_API_SECRET",
        "POLYMARKET_API_PASSPHRASE", "AI_API_KEY", "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID", "TRADING_MODE", "POLYMARKET_ALLOW_LIVE_TRADING",
    ):
        monkeypatch.delenv(name, raising=False)
    resolved = load_settings(config_path=config_path, env_file=tmp_path / "absent.env")
    resolved.repo_root = tmp_path
    resolved.data_dir = str(tmp_path / "data")
    resolved.cache_dir = str(tmp_path / "data" / "cache")
    resolved.log_dir = str(tmp_path / "logs")
    resolved.database_path = str(tmp_path / "data" / "test.db")
    resolved.ensure_dirs()
    return resolved


@pytest.fixture
def live_settings(settings, monkeypatch) -> Settings:
    monkeypatch.setenv("POLYMARKET_ALLOW_LIVE_TRADING", "true")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "ab" * 32)
    settings.mode = "live"
    settings.live_trading = True
    settings.config["trading"]["mode"] = "live"
    settings.config["trading"]["live_trading"] = True
    settings.secrets = Secrets(polymarket_private_key="0x" + "ab" * 32)
    return settings


@pytest.fixture
def database(settings):
    from src.storage.database import Database

    db = Database(settings.database_path).connect()
    yield db
    db.close()


# ------------------------------------------------------------------- factories
def make_match(
    home: str,
    away: str,
    home_goals: int,
    away_goals: int,
    date: str = "2025-08-15",
    league: str = "E0",
    home_corners: int | None = 6,
    away_corners: int | None = 4,
    home_shots: int | None = 14,
    away_shots: int | None = 10,
    odds: dict | None = None,
) -> MatchRecord:
    raw = {
        "home_team": home, "away_team": away, "home_goals": home_goals, "away_goals": away_goals,
        "match_date": date, "league": "Premier League", "league_code": league, "season": "2526",
        "home_corners": home_corners, "away_corners": away_corners,
        "home_shots": home_shots, "away_shots": away_shots,
        "home_shots_on_target": 5 if home_shots else None,
        "away_shots_on_target": 3 if away_shots else None,
        "home_yellows": 2, "away_yellows": 3, "home_reds": 0, "away_reds": 0,
        "source": "test", "odds": odds or {},
    }
    return Normalizer.match_from_raw(raw)


@pytest.fixture
def synthetic_history() -> TeamHistory:
    """A deterministic synthetic league: strong/weak teams, stable rates."""
    rng = random.Random(42)
    teams = [f"Team {chr(65 + i)}" for i in range(10)]
    strengths = {team: 1.6 - 0.12 * i for i, team in enumerate(teams)}
    matches: list[MatchRecord] = []
    day = 0
    for _round_index in range(40):
        for i in range(0, len(teams), 2):
            home, away = teams[i], teams[(i + 1) % len(teams)]
            if home == away:
                continue
            day += 1
            home_strength = strengths[home]
            away_strength = strengths[away]
            home_goals = max(0, min(6, int(round(rng.gauss(home_strength * 1.15, 1.0)))))
            away_goals = max(0, min(6, int(round(rng.gauss(away_strength * 0.95, 1.0)))))
            matches.append(make_match(
                home, away, home_goals, away_goals,
                date=f"2025-{1 + (day // 28):02d}-{1 + (day % 28):02d}",
                home_corners=rng.randint(2, 11), away_corners=rng.randint(1, 9),
                home_shots=rng.randint(5, 22), away_shots=rng.randint(3, 18),
                odds={
                    "bet365": {"home": 1.8, "draw": 3.6, "away": 4.5, "over25": 1.85, "under25": 1.95},
                    "pinnacle": {"home": 1.82, "draw": 3.7, "away": 4.6, "over25": 1.87, "under25": 1.93},
                },
            ))
    return TeamHistory(matches)


@pytest.fixture
def synthetic_players() -> list[PlayerRecord]:
    players = []
    for _team_index, team in enumerate(["Team A", "Team B"]):
        for position_index, name in enumerate([f"Striker {team}", f"Mid {team}", f"Def {team}"]):
            players.append(Normalizer.player_from_raw(
                {
                    "player_key": f"{team.lower()}|{name.lower()}",
                    "display_name": name, "team": team, "position": "FWD" if position_index == 0 else "MID",
                    "minutes": 900.0, "starts": 10.0, "games": 10.0,
                    "goals": 6.0 - position_index, "assists": 2.0,
                    "xg": 5.5 - position_index, "xa": 1.8,
                    "xg_per_90": 0.55 - 0.15 * position_index,
                    "xa_per_90": 0.18, "goals_per_90": 0.6 - 0.15 * position_index,
                    "assists_per_90": 0.2, "availability": 1.0, "status": "a",
                    "source": "test", "attributes": {"penalty_order": 1 if position_index == 0 else None},
                },
                {"starting_probability": 0.9 - 0.1 * position_index, "expected_minutes": 82.0},
            ))
    return players


def _utc_iso(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_market(
    market_id: str = "MKT-1",
    market_type: MarketType = MarketType.MATCH_RESULT,
    selection: str = "Team A",
    home: str = "Team A",
    away: str = "Team B",
    bid: float | None = 0.30,
    ask: float | None = 0.32,
    liquidity: float = 5000.0,
    ts: float | None = None,
    spread: float | None = None,
    player: str = "",
    line: float | None = None,
    selection_kind: str = "TEAM",
    event_id: str = "EVT-1",
    end_time: str | None = None,
    start_time: str | None = None,
    league: str = "Premier League",
) -> Market:
    now = ts if ts is not None else time.time()
    # Use real UTC, not local time labelled 'Z': the latter would shift every
    # fixture by the machine's UTC offset and silently break time-based filters.
    end = end_time or _utc_iso(now + 6 * 3600)
    start = start_time or _utc_iso(now + 3600)
    snapshot = MarketSnapshot(
        ts=now, bid=bid, ask=ask, spread=spread, liquidity=liquidity,
        volume_24h=1000.0, source="test", price=((bid + ask) / 2) if bid and ask else ask,
    )
    return Market(
        market_id=market_id, question=f"Will {selection} win?", slug=f"slug-{market_id}",
        event_id=event_id, event_title=f"{home} vs. {away}", league=league,
        home_team=home, away_team=away, player=player, market_type=market_type,
        classification_confidence=0.95, selection=selection, selection_kind=selection_kind,
        line=line, yes_token_id=f"token-{market_id}", no_token_id=f"token-no-{market_id}",
        tick_size=0.01, min_order_size=5.0, start_time=start, end_time=end,
        snapshot=snapshot, raw={},
    )


@pytest.fixture
def market_factory():
    return make_market


@pytest.fixture
def match_factory():
    return make_match


# ---------------------------------------------------------------- fake polymarket
def gamma_market_payload(**overrides) -> dict:
    payload = {
        "id": "123456",
        "question": "Will Arsenal win on 2026-03-13?",
        "slug": "will-arsenal-win-on-2026-03-13",
        "groupItemTitle": "Arsenal",
        "sportsMarketType": "moneyline",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.51", "0.49"]),
        "clobTokenIds": json.dumps(["111", "222"]),
        "bestBid": 0.49,
        "bestAsk": 0.52,
        "spread": 0.03,
        "liquidityNum": 2500.0,
        "volume24hr": 900.0,
        "orderPriceMinTickSize": 0.01,
        "orderMinSize": 5,
        "negRisk": False,
        "endDateIso": "2026-03-13",
        "gameStartTime": "2026-03-13 20:00:00+00",
        "resolutionSource": "https://www.premierleague.com/",
        "description": "Resolves Yes if Arsenal win.",
    }
    payload.update(overrides)
    return payload


def gamma_event_payload(**overrides) -> dict:
    payload = {
        "id": "999",
        "ticker": "epl-ars-che-2026-03-13",
        "slug": "arsenal-vs-chelsea",
        "title": "Arsenal vs. Chelsea",
        "startTime": "2026-03-13T20:00:00Z",
        "endDate": "2026-03-13T22:00:00Z",
        "series": [{"id": "10188", "title": "Premier League", "slug": "premier-league"}],
        "tags": [{"id": "100350", "slug": "soccer", "label": "Soccer"}],
        "markets": [gamma_market_payload()],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def fake_gamma_client() -> FakeHttpClient:
    return FakeHttpClient({
        "https://gamma-api.polymarket.com/tags/slug/soccer": {"id": "100350", "slug": "soccer", "label": "Soccer"},
        "https://gamma-api.polymarket.com/sports": [{"id": 1, "sport": "epl", "name": "Premier League"}],
        "https://gamma-api.polymarket.com/events/keyset": {"events": [gamma_event_payload()], "next_cursor": None},
        "https://gamma-api.polymarket.com/events/999": gamma_event_payload(),
    })


@pytest.fixture
def fake_clob_client() -> FakeHttpClient:
    return FakeHttpClient({
        "https://clob.polymarket.com/book": {
            "market": "0xabc", "asset_id": "111", "timestamp": str(int(time.time() * 1000)),
            "bids": [{"price": "0.48", "size": "400"}, {"price": "0.47", "size": "900"}],
            "asks": [{"price": "0.52", "size": "300"}, {"price": "0.53", "size": "1200"}],
        },
        "https://clob.polymarket.com/midpoint": {"mid": "0.50"},
        "https://clob.polymarket.com/price": {"price": "0.52"},
        "https://clob.polymarket.com/fee-rate": {"base_fee": 0},
        "https://clob.polymarket.com/sampling-markets": [{"id": "1"}],
    })


@pytest.fixture
def discovery(fake_gamma_client, fake_clob_client, settings) -> MarketDiscovery:
    from src.data.providers.polymarket import ClobProvider, GammaProvider

    gamma = GammaProvider(client=fake_gamma_client, host="https://gamma-api.polymarket.com")
    clob = ClobProvider(client=fake_clob_client, host="https://clob.polymarket.com")
    return MarketDiscovery(gamma=gamma, clob=clob, settings=settings)
