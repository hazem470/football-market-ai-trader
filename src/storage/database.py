"""SQLite persistence layer (SQLite now, PostgreSQL-ready by design).

All access goes through :class:`Database`, which owns the schema and returns
plain dicts / typed records. Swapping in PostgreSQL later means re-implementing
this one class - no other module touches SQL.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    handle        TEXT UNIQUE,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
    name          TEXT PRIMARY KEY,
    kind          TEXT,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_ok_at    REAL,
    last_error    TEXT,
    health        TEXT DEFAULT 'UNKNOWN',
    stats_json    TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    match_key     TEXT PRIMARY KEY,     -- normalized "<date>|<home>|<away>"
    league        TEXT,
    season        TEXT,
    match_date    TEXT,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    home_goals    INTEGER,
    away_goals    INTEGER,
    home_corners  INTEGER,
    away_corners  INTEGER,
    home_shots    INTEGER,
    away_shots    INTEGER,
    home_shots_on_target INTEGER,
    away_shots_on_target INTEGER,
    home_yellows  INTEGER,
    away_yellows  INTEGER,
    home_reds     INTEGER,
    away_reds     INTEGER,
    source        TEXT,
    updated_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_league_date ON matches(league, match_date);
CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(home_team, away_team);

CREATE TABLE IF NOT EXISTS players (
    player_key    TEXT PRIMARY KEY,     -- normalized "<team>|<player>"
    team          TEXT,
    display_name  TEXT,
    position      TEXT,
    provider      TEXT,
    updated_at    REAL NOT NULL,
    attributes_json TEXT
);

CREATE TABLE IF NOT EXISTS markets (
    market_id     TEXT PRIMARY KEY,
    event_id      TEXT,
    slug          TEXT,
    question      TEXT,
    league        TEXT,
    home_team     TEXT,
    away_team     TEXT,
    player        TEXT,
    market_type   TEXT,
    classification_confidence REAL,
    selection     TEXT,
    yes_token_id  TEXT,
    no_token_id   TEXT,
    start_time    TEXT,
    end_time      TEXT,
    tick_size     REAL,
    min_order_size REAL,
    neg_risk      INTEGER,
    resolution_source TEXT,
    resolution_rules  TEXT,
    discovered_at REAL NOT NULL,
    raw_json      TEXT
);

CREATE INDEX IF NOT EXISTS idx_markets_type ON markets(market_type);
CREATE INDEX IF NOT EXISTS idx_markets_event ON markets(event_id);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id     TEXT NOT NULL,
    token_id      TEXT,
    ts            REAL NOT NULL,
    price         REAL,
    bid           REAL,
    ask           REAL,
    spread        REAL,
    liquidity     REAL,
    volume_24h    REAL,
    mid           REAL,
    source        TEXT,
    FOREIGN KEY(market_id) REFERENCES markets(market_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_market_ts ON market_snapshots(market_id, ts);

CREATE TABLE IF NOT EXISTS features (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_key   TEXT UNIQUE NOT NULL,
    match_key     TEXT,
    market_id     TEXT,
    created_at    REAL NOT NULL,
    feature_version TEXT,
    data_timestamp REAL,
    payload_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_key TEXT UNIQUE NOT NULL,
    market_id     TEXT,
    match_key     TEXT,
    created_at    REAL NOT NULL,
    model_name    TEXT,
    model_version TEXT,
    feature_version TEXT,
    calibration_version TEXT,
    data_timestamp REAL,
    raw_probability REAL,
    calibrated_probability REAL,
    confidence    REAL,
    payload_json  TEXT
);

CREATE INDEX IF NOT EXISTS idx_predictions_market ON predictions(market_id, created_at);

CREATE TABLE IF NOT EXISTS signals (
    signal_id     TEXT PRIMARY KEY,
    market_id     TEXT,
    match_key     TEXT,
    created_at    REAL NOT NULL,
    state         TEXT NOT NULL,          -- BUY | SELL | NO_TRADE | INSUFFICIENT_DATA | UNSUPPORTED
    market_type   TEXT,
    selection     TEXT,
    model_probability REAL,
    calibrated_probability REAL,
    market_price  REAL,
    edge          REAL,
    confidence    REAL,
    recommended_size REAL,
    explanation   TEXT,
    trace_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

CREATE TABLE IF NOT EXISTS orders (
    order_id      TEXT PRIMARY KEY,
    client_order_id TEXT UNIQUE,
    signal_id     TEXT,
    market_id     TEXT,
    token_id      TEXT,
    side          TEXT,
    price         REAL,
    size          REAL,
    order_type    TEXT,
    status        TEXT,                   -- PENDING | OPEN | FILLED | PARTIAL | REJECTED | CANCELLED
    mode          TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL,
    exchange_order_id TEXT,
    filled_size   REAL DEFAULT 0,
    avg_fill_price REAL,
    error         TEXT,
    raw_json      TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_market ON orders(market_id);

CREATE TABLE IF NOT EXISTS positions (
    position_id   TEXT PRIMARY KEY,
    market_id     TEXT,
    token_id      TEXT,
    match_key     TEXT,
    selection     TEXT,
    market_type   TEXT,
    side          TEXT,
    size          REAL,
    entry_price   REAL,
    current_price REAL,
    opened_at     REAL,
    closed_at     REAL,
    exit_price    REAL,
    realized_pnl  REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    status        TEXT,                   -- OPEN | CLOSED
    mode          TEXT,
    signal_id     TEXT,
    model_probability REAL,
    payload_json  TEXT
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);

CREATE TABLE IF NOT EXISTS trades (
    trade_id      TEXT PRIMARY KEY,
    order_id      TEXT,
    position_id   TEXT,
    market_id     TEXT,
    ts            REAL NOT NULL,
    action        TEXT,                   -- OPEN | CLOSE
    price         REAL,
    size          REAL,
    fee           REAL DEFAULT 0,
    slippage      REAL DEFAULT 0,
    pnl           REAL DEFAULT 0,
    mode          TEXT
);

CREATE TABLE IF NOT EXISTS pnl (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    mode          TEXT,
    balance       REAL,
    realized_pnl  REAL,
    unrealized_pnl REAL,
    exposure      REAL,
    open_positions INTEGER,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS risk_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    kind          TEXT,
    severity      TEXT,
    market_id     TEXT,
    detail        TEXT,
    payload_json  TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    channel       TEXT,
    event         TEXT,
    status        TEXT,
    detail        TEXT
);

CREATE TABLE IF NOT EXISTS backtests (
    run_id        TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    config_json   TEXT,
    metrics_json  TEXT,
    status        TEXT
);

CREATE TABLE IF NOT EXISTS system_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    level         TEXT,
    component     TEXT,
    event         TEXT,
    message       TEXT
);
"""


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"_unserialisable": str(type(value))})


_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_column(name: str) -> str:
    """Assert that a column name is a bare identifier before interpolating it.

    Defence in depth on top of the per-table allowlists: even if a future caller
    bypassed the allowlist, a name containing anything but word characters is
    rejected here rather than reaching SQL. Values are always bound parameters.
    """
    if not _COLUMN_RE.match(name):
        raise ValueError(f"unsafe SQL column identifier: {name!r}")
    return name


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # reject NaN


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return None if number is None else int(number)


@dataclass
class Database:
    """Thin, thread-safe SQLite wrapper.

    Usage::

        db = Database("data/football_trader.db")
        db.connect()
        db.insert_signal(signal_dict)
    """

    path: str | Path = "data/football_trader.db"
    _conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    # ------------------------------------------------------------- plumbing
    def connect(self) -> Database:
        self.path = Path(self.path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30.0, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
        return self

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> Database:
        return self.connect()

    def __exit__(self, *_exc) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def table_names(self) -> list[str]:
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [r["name"] for r in rows]

    # ------------------------------------------------------------- matches
    def upsert_match(self, match: dict) -> None:
        payload = (
            match["match_key"],
            match.get("league"),
            match.get("season"),
            match.get("match_date"),
            match["home_team"],
            match["away_team"],
            _to_int(match.get("home_goals")),
            _to_int(match.get("away_goals")),
            _to_int(match.get("home_corners")),
            _to_int(match.get("away_corners")),
            _to_int(match.get("home_shots")),
            _to_int(match.get("away_shots")),
            _to_int(match.get("home_shots_on_target")),
            _to_int(match.get("away_shots_on_target")),
            _to_int(match.get("home_yellows")),
            _to_int(match.get("away_yellows")),
            _to_int(match.get("home_reds")),
            _to_int(match.get("away_reds")),
            match.get("source"),
            time.time(),
        )
        self.execute(
            """
            INSERT INTO matches (match_key, league, season, match_date, home_team, away_team,
                home_goals, away_goals, home_corners, away_corners, home_shots, away_shots,
                home_shots_on_target, away_shots_on_target, home_yellows, away_yellows,
                home_reds, away_reds, source, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(match_key) DO UPDATE SET
                home_goals=excluded.home_goals, away_goals=excluded.away_goals,
                home_corners=excluded.home_corners, away_corners=excluded.away_corners,
                home_shots=excluded.home_shots, away_shots=excluded.away_shots,
                home_shots_on_target=excluded.home_shots_on_target,
                away_shots_on_target=excluded.away_shots_on_target,
                home_yellows=excluded.home_yellows, away_yellows=excluded.away_yellows,
                home_reds=excluded.home_reds, away_reds=excluded.away_reds,
                source=excluded.source, updated_at=excluded.updated_at
            """,
            payload,
        )

    def upsert_matches(self, matches: Iterable[dict]) -> int:
        count = 0
        with self.transaction() as conn:
            for match in matches:
                self.upsert_match(match)
                count += 1
            conn.execute("SELECT 1")
        return count

    def list_matches(self, league: str | None = None, finished_only: bool = True, limit: int = 100000) -> list[dict]:
        sql = "SELECT * FROM matches WHERE 1=1"
        params: list[Any] = []
        if league:
            sql += " AND league = ?"
            params.append(league)
        if finished_only:
            sql += " AND home_goals IS NOT NULL AND away_goals IS NOT NULL"
        sql += " ORDER BY match_date ASC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def match_count(self) -> int:
        row = self.query_one("SELECT COUNT(*) AS n FROM matches")
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------- players
    def upsert_player(self, player: dict) -> None:
        self.execute(
            """
            INSERT INTO players (player_key, team, display_name, position, provider, updated_at, attributes_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(player_key) DO UPDATE SET
                team=excluded.team, display_name=excluded.display_name, position=excluded.position,
                provider=excluded.provider, updated_at=excluded.updated_at,
                attributes_json=excluded.attributes_json
            """,
            (
                player["player_key"],
                player.get("team"),
                player.get("display_name"),
                player.get("position"),
                player.get("provider"),
                time.time(),
                _json_dumps(player.get("attributes", {})),
            ),
        )

    # ------------------------------------------------------------- markets
    def upsert_market(self, market: dict) -> None:
        self.execute(
            """
            INSERT INTO markets (market_id, event_id, slug, question, league, home_team, away_team,
                player, market_type, classification_confidence, selection, yes_token_id, no_token_id,
                start_time, end_time, tick_size, min_order_size, neg_risk, resolution_source,
                resolution_rules, discovered_at, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(market_id) DO UPDATE SET
                market_type=excluded.market_type,
                classification_confidence=excluded.classification_confidence,
                selection=excluded.selection,
                start_time=excluded.start_time, end_time=excluded.end_time,
                tick_size=excluded.tick_size, min_order_size=excluded.min_order_size,
                resolution_source=excluded.resolution_source, resolution_rules=excluded.resolution_rules,
                raw_json=excluded.raw_json
            """,
            (
                market["market_id"],
                market.get("event_id"),
                market.get("slug"),
                market.get("question"),
                market.get("league"),
                market.get("home_team"),
                market.get("away_team"),
                market.get("player"),
                market.get("market_type"),
                _to_float(market.get("classification_confidence")),
                market.get("selection"),
                market.get("yes_token_id"),
                market.get("no_token_id"),
                market.get("start_time"),
                market.get("end_time"),
                _to_float(market.get("tick_size")),
                _to_float(market.get("min_order_size")),
                1 if market.get("neg_risk") else 0,
                market.get("resolution_source"),
                market.get("resolution_rules"),
                time.time(),
                _json_dumps(market.get("raw", {})),
            ),
        )

    def upsert_markets(self, markets: Iterable[dict]) -> int:
        count = 0
        with self.transaction() as conn:
            for market in markets:
                self.upsert_market(market)
                count += 1
            conn.execute("SELECT 1")
        return count

    def list_markets(self, market_type: str | None = None, limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM markets WHERE 1=1"
        params: list[Any] = []
        if market_type:
            sql += " AND market_type = ?"
            params.append(market_type)
        sql += " ORDER BY discovered_at DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def insert_snapshot(self, snapshot: dict) -> None:
        self.execute(
            """
            INSERT INTO market_snapshots (market_id, token_id, ts, price, bid, ask, spread,
                liquidity, volume_24h, mid, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot.get("market_id"),
                snapshot.get("token_id"),
                float(snapshot.get("ts") or time.time()),
                _to_float(snapshot.get("price")),
                _to_float(snapshot.get("bid")),
                _to_float(snapshot.get("ask")),
                _to_float(snapshot.get("spread")),
                _to_float(snapshot.get("liquidity")),
                _to_float(snapshot.get("volume_24h")),
                _to_float(snapshot.get("mid")),
                snapshot.get("source"),
            ),
        )

    # ------------------------------------------------------------- features
    def insert_features(self, record: dict) -> str:
        key = record.get("feature_key") or f"FEAT-{uuid.uuid4().hex[:16]}"
        self.execute(
            """
            INSERT OR REPLACE INTO features (feature_key, match_key, market_id, created_at,
                feature_version, data_timestamp, payload_json)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                key,
                record.get("match_key"),
                record.get("market_id"),
                time.time(),
                record.get("feature_version"),
                _to_float(record.get("data_timestamp")),
                _json_dumps(record.get("payload", {})),
            ),
        )
        return key

    # ------------------------------------------------------------- predictions
    def insert_prediction(self, record: dict) -> str:
        key = record.get("prediction_key") or f"PRED-{uuid.uuid4().hex[:16]}"
        self.execute(
            """
            INSERT OR REPLACE INTO predictions (prediction_key, market_id, match_key, created_at,
                model_name, model_version, feature_version, calibration_version, data_timestamp,
                raw_probability, calibrated_probability, confidence, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key,
                record.get("market_id"),
                record.get("match_key"),
                time.time(),
                record.get("model_name"),
                record.get("model_version"),
                record.get("feature_version"),
                record.get("calibration_version"),
                _to_float(record.get("data_timestamp")),
                _to_float(record.get("raw_probability")),
                _to_float(record.get("calibrated_probability")),
                _to_float(record.get("confidence")),
                _json_dumps(record.get("payload", {})),
            ),
        )
        return key

    # ------------------------------------------------------------- signals
    def insert_signal(self, signal: dict) -> str:
        signal_id = signal.get("signal_id") or f"SIG-{time.strftime('%Y-%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        self.execute(
            """
            INSERT OR REPLACE INTO signals (signal_id, market_id, match_key, created_at, state,
                market_type, selection, model_probability, calibrated_probability, market_price,
                edge, confidence, recommended_size, explanation, trace_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal_id,
                signal.get("market_id"),
                signal.get("match_key"),
                float(signal.get("created_at") or time.time()),
                signal.get("state"),
                signal.get("market_type"),
                signal.get("selection"),
                _to_float(signal.get("model_probability")),
                _to_float(signal.get("calibrated_probability")),
                _to_float(signal.get("market_price")),
                _to_float(signal.get("edge")),
                _to_float(signal.get("confidence")),
                _to_float(signal.get("recommended_size")),
                signal.get("explanation"),
                _json_dumps(signal.get("trace", {})),
            ),
        )
        return signal_id

    def list_signals(self, limit: int = 200, states: Iterable[str] | None = None) -> list[dict]:
        sql = "SELECT * FROM signals WHERE 1=1"
        params: list[Any] = []
        states = list(states or [])
        if states:
            sql += f" AND state IN ({','.join('?' * len(states))})"
            params.extend(states)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    # ------------------------------------------------------------- orders
    def insert_order(self, order: dict) -> str:
        order_id = order.get("order_id") or f"ORD-{uuid.uuid4().hex[:12].upper()}"
        self.execute(
            """
            INSERT INTO orders (order_id, client_order_id, signal_id, market_id, token_id, side,
                price, size, order_type, status, mode, created_at, updated_at, exchange_order_id,
                filled_size, avg_fill_price, error, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                order_id,
                order.get("client_order_id"),
                order.get("signal_id"),
                order.get("market_id"),
                order.get("token_id"),
                order.get("side"),
                _to_float(order.get("price")),
                _to_float(order.get("size")),
                order.get("order_type"),
                order.get("status") or "PENDING",
                order.get("mode"),
                float(order.get("created_at") or time.time()),
                time.time(),
                order.get("exchange_order_id"),
                _to_float(order.get("filled_size")) or 0.0,
                _to_float(order.get("avg_fill_price")),
                order.get("error"),
                _json_dumps(order.get("raw", {})),
            ),
        )
        return order_id

    #: Columns that may appear in a generated UPDATE. Every name is interpolated
    #: from THIS set, never from caller input, and all values are bound
    #: parameters, so the statement cannot be influenced by data.
    _ORDER_UPDATABLE: frozenset[str] = frozenset(
        {"status", "exchange_order_id", "filled_size", "avg_fill_price",
         "error", "raw_json", "price", "size"}
    )

    def update_order(self, order_id: str, **fields) -> None:
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in self._ORDER_UPDATABLE:
                continue
            sets.append(f"{_safe_column(key)} = ?")
            params.append(_json_dumps(value) if key == "raw_json" else value)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(order_id)
        self.execute(f"UPDATE orders SET {', '.join(sets)} WHERE order_id = ?", params)  # nosec B608

    def list_orders(self, status: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM orders WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    # ------------------------------------------------------------- positions
    def insert_position(self, position: dict) -> str:
        position_id = position.get("position_id") or f"POS-{uuid.uuid4().hex[:12].upper()}"
        self.execute(
            """
            INSERT INTO positions (position_id, market_id, token_id, match_key, selection, market_type,
                side, size, entry_price, current_price, opened_at, closed_at, exit_price,
                realized_pnl, unrealized_pnl, status, mode, signal_id, model_probability, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                position_id,
                position.get("market_id"),
                position.get("token_id"),
                position.get("match_key"),
                position.get("selection"),
                position.get("market_type"),
                position.get("side"),
                _to_float(position.get("size")),
                _to_float(position.get("entry_price")),
                _to_float(position.get("current_price")),
                float(position.get("opened_at") or time.time()),
                _to_float(position.get("closed_at")),
                _to_float(position.get("exit_price")),
                _to_float(position.get("realized_pnl")) or 0.0,
                _to_float(position.get("unrealized_pnl")) or 0.0,
                position.get("status") or "OPEN",
                position.get("mode"),
                position.get("signal_id"),
                _to_float(position.get("model_probability")),
                _json_dumps(position.get("payload", {})),
            ),
        )
        return position_id

    #: See `_ORDER_UPDATABLE`.
    _POSITION_UPDATABLE: frozenset[str] = frozenset(
        {"current_price", "closed_at", "exit_price", "realized_pnl",
         "unrealized_pnl", "status", "size", "payload_json"}
    )

    def update_position(self, position_id: str, **fields) -> None:
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in self._POSITION_UPDATABLE:
                continue
            sets.append(f"{_safe_column(key)} = ?")
            params.append(_json_dumps(value) if key == "payload_json" else value)
        if not sets:
            return
        params.append(position_id)
        self.execute(f"UPDATE positions SET {', '.join(sets)} WHERE position_id = ?", params)  # nosec B608

    def list_positions(self, status: str | None = "OPEN", mode: str | None = None, limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM positions WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if mode:
            sql += " AND mode = ?"
            params.append(mode)
        sql += " ORDER BY opened_at DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def open_exposure(self, mode: str | None = None, since_ts: float | None = None) -> float:
        sql = "SELECT COALESCE(SUM(size * entry_price), 0) AS exposure FROM positions WHERE status='OPEN'"
        params: list[Any] = []
        if mode:
            sql += " AND mode = ?"
            params.append(mode)
        if since_ts:
            sql += " AND opened_at >= ?"
            params.append(since_ts)
        row = self.query_one(sql, params)
        return float(row["exposure"]) if row else 0.0

    def realized_pnl_since(self, since_ts: float, mode: str | None = None) -> float:
        sql = "SELECT COALESCE(SUM(realized_pnl), 0) AS pnl FROM positions WHERE closed_at >= ?"
        params: list[Any] = [since_ts]
        if mode:
            sql += " AND mode = ?"
            params.append(mode)
        row = self.query_one(sql, params)
        return float(row["pnl"]) if row else 0.0

    # ------------------------------------------------------------- trades/pnl
    def insert_trade(self, trade: dict) -> str:
        trade_id = trade.get("trade_id") or f"TRD-{uuid.uuid4().hex[:12].upper()}"
        self.execute(
            """
            INSERT INTO trades (trade_id, order_id, position_id, market_id, ts, action, price, size,
                fee, slippage, pnl, mode)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade_id,
                trade.get("order_id"),
                trade.get("position_id"),
                trade.get("market_id"),
                float(trade.get("ts") or time.time()),
                trade.get("action"),
                _to_float(trade.get("price")),
                _to_float(trade.get("size")),
                _to_float(trade.get("fee")) or 0.0,
                _to_float(trade.get("slippage")) or 0.0,
                _to_float(trade.get("pnl")) or 0.0,
                trade.get("mode"),
            ),
        )
        return trade_id

    def list_trades(self, limit: int = 500, mode: str | None = None) -> list[dict]:
        sql = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []
        if mode:
            sql += " AND mode = ?"
            params.append(mode)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def record_pnl(self, record: dict) -> None:
        self.execute(
            """
            INSERT INTO pnl (ts, mode, balance, realized_pnl, unrealized_pnl, exposure, open_positions, note)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                float(record.get("ts") or time.time()),
                record.get("mode"),
                _to_float(record.get("balance")),
                _to_float(record.get("realized_pnl")),
                _to_float(record.get("unrealized_pnl")),
                _to_float(record.get("exposure")),
                _to_int(record.get("open_positions")),
                record.get("note"),
            ),
        )

    def latest_pnl(self) -> dict | None:
        return self.query_one("SELECT * FROM pnl ORDER BY ts DESC LIMIT 1")

    # ------------------------------------------------------------- risk/logs
    def record_risk_event(self, event: dict) -> None:
        self.execute(
            """
            INSERT INTO risk_events (ts, kind, severity, market_id, detail, payload_json)
            VALUES (?,?,?,?,?,?)
            """,
            (
                float(event.get("ts") or time.time()),
                event.get("kind"),
                event.get("severity"),
                event.get("market_id"),
                event.get("detail"),
                _json_dumps(event.get("payload", {})),
            ),
        )

    def list_risk_events(self, limit: int = 100) -> list[dict]:
        return self.query("SELECT * FROM risk_events ORDER BY ts DESC LIMIT ?", (limit,))

    def record_notification(self, record: dict) -> None:
        self.execute(
            "INSERT INTO notifications (ts, channel, event, status, detail) VALUES (?,?,?,?,?)",
            (
                float(record.get("ts") or time.time()),
                record.get("channel"),
                record.get("event"),
                record.get("status"),
                record.get("detail"),
            ),
        )

    def log(self, level: str, component: str, event: str, message: str) -> None:
        self.execute(
            "INSERT INTO system_logs (ts, level, component, event, message) VALUES (?,?,?,?,?)",
            (time.time(), level, component, event, message),
        )

    def list_logs(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM system_logs ORDER BY ts DESC LIMIT ?", (limit,))

    def save_backtest(self, run_id: str, config: dict, metrics: dict, status: str = "DONE") -> None:
        self.execute(
            "INSERT OR REPLACE INTO backtests (run_id, created_at, config_json, metrics_json, status) VALUES (?,?,?,?,?)",
            (run_id, time.time(), _json_dumps(config), _json_dumps(metrics), status),
        )

    def list_backtests(self, limit: int = 50) -> list[dict]:
        return self.query("SELECT * FROM backtests ORDER BY created_at DESC LIMIT ?", (limit,))

    def upsert_provider_health(self, name: str, kind: str, health: str, detail: str = "", stats: dict | None = None) -> None:
        self.execute(
            """
            INSERT INTO providers (name, kind, enabled, last_ok_at, last_error, health, stats_json)
            VALUES (?,?,1,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                health=excluded.health,
                last_ok_at=CASE WHEN excluded.health='HEALTHY' THEN excluded.last_ok_at ELSE providers.last_ok_at END,
                last_error=excluded.last_error,
                stats_json=excluded.stats_json
            """,
            (
                name,
                kind,
                time.time(),
                None if health == "HEALTHY" else detail,
                health,
                _json_dumps(stats or {}),
            ),
        )

    def list_providers(self) -> list[dict]:
        return self.query("SELECT * FROM providers ORDER BY name")
