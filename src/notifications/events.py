"""Notification event formatting and thresholding."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.notifications.telegram import TelegramNotifier
from src.strategy.signals import Signal


class NotifyEvent:
    OPPORTUNITY = "opportunity"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_FILLED = "order_filled"
    POSITION_CLOSED = "position_closed"
    DAILY_SUMMARY = "daily_summary"
    RISK_LIMIT = "risk_limit_reached"
    PROVIDER_FAILURE = "provider_failure"
    EXECUTION_FAILURE = "execution_failure"
    STOPPED = "stopped"
    CIRCUIT_BREAKER = "circuit_breaker"

    ALL = (
        OPPORTUNITY, ORDER_SUBMITTED, ORDER_FILLED, POSITION_CLOSED, DAILY_SUMMARY,
        RISK_LIMIT, PROVIDER_FAILURE, EXECUTION_FAILURE, STOPPED, CIRCUIT_BREAKER,
    )


@dataclass
class NotificationCenter:
    """Routes events to the configured channels with per-event gating."""

    notifier: TelegramNotifier | None = None
    console: bool = True
    min_edge_to_notify: float = 0.08
    notify_on: tuple[str, ...] = NotifyEvent.ALL
    database: object | None = None
    log: list[dict] = field(default_factory=list)

    def _allowed(self, event: str) -> bool:
        return event in self.notify_on

    def emit(self, event: str, title: str, message: str, silent: bool = False) -> None:
        if not self._allowed(event):
            return
        stamp = time.strftime("%H:%M:%S")
        if self.console:
            print(f"[{stamp}] [{event}] {title}: {message}")
        self.log.append({"ts": time.time(), "event": event, "title": title, "message": message})
        if self.notifier is not None and self.notifier.available():
            self.notifier.send(f"<b>{title}</b>\n{message}", event=event, silent=silent)

    # -------------------------------------------------------------- specific
    def opportunity(self, signal: Signal) -> None:
        if signal.edge < self.min_edge_to_notify:
            return
        self.emit(
            NotifyEvent.OPPORTUNITY,
            f"Opportunity: edge {signal.edge:+.2%}",
            (
                f"{signal.label}\n"
                f"market type: {signal.market_type}\n"
                f"model: {signal.model_name}\n"
                f"model prob {signal.model_probability:.1%} -> calibrated "
                f"{signal.calibrated_probability:.1%}\n"
                f"market price {signal.market_price:.1%} | edge {signal.edge:+.2%}\n"
                f"confidence {signal.confidence:.2f} | size {signal.recommended_size:.2f}\n"
                f"signal {signal.signal_id}"
            ),
        )

    def order_submitted(self, order: dict) -> None:
        self.emit(NotifyEvent.ORDER_SUBMITTED, "Order submitted",
                  f"{order.get('market_id')} {order.get('side')} {order.get('size')} "
                  f"@ {order.get('price')} ({order.get('status')})")

    def order_filled(self, order: dict) -> None:
        self.emit(NotifyEvent.ORDER_FILLED, "Order filled",
                  f"{order.get('market_id')} filled {order.get('filled_size')} @ "
                  f"{order.get('avg_fill_price')} slippage {order.get('slippage')}")

    def position_closed(self, position: dict) -> None:
        self.emit(NotifyEvent.POSITION_CLOSED, "Position closed",
                  f"{position.get('market_id')} reason={position.get('exit_reason')} "
                  f"P/L {position.get('realized_pnl')}", silent=True)

    def risk_limit(self, reason: str, detail: str = "") -> None:
        self.emit(NotifyEvent.RISK_LIMIT, "Risk limit reached", f"{reason}\n{detail}")

    def provider_failure(self, provider: str, error: str) -> None:
        self.emit(NotifyEvent.PROVIDER_FAILURE, f"Provider failure: {provider}", error)

    def execution_failure(self, detail: str) -> None:
        self.emit(NotifyEvent.EXECUTION_FAILURE, "Execution failure", detail)

    def circuit_breaker(self, reason: str, detail: str = "") -> None:
        self.emit(NotifyEvent.CIRCUIT_BREAKER, "CIRCUIT BREAKER", f"{reason}\n{detail}")

    def stopped(self, reason: str) -> None:
        self.emit(NotifyEvent.STOPPED, "Bot stopped", reason)

    def daily_summary(self, summary: dict) -> None:
        self.emit(
            NotifyEvent.DAILY_SUMMARY, "Daily summary",
            "\n".join(f"{key}: {value}" for key, value in summary.items()),
            silent=True,
        )
