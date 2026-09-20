"""Telegram notifications via the official Bot API.

The user creates THEIR OWN bot with @BotFather and supplies the token. This
project never logs into a personal Telegram account and never sends a secret to
Telegram - the credential-redaction layer strips anything that looks like one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.core.http import HttpClient, HttpError

API_ROOT = "https://api.telegram.org"


@dataclass
class TelegramNotifier:
    client: HttpClient | None = None
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = False
    database: object | None = None
    sent: list[dict] = field(default_factory=list)
    max_retries: int = 2

    def available(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_id and self.client is not None)

    def send(self, message: str, event: str = "generic", silent: bool = False) -> bool:
        if not self.available():
            return False
        payload = {
            "chat_id": self.chat_id,
            "text": message[:4000],
            "parse_mode": "HTML",
            "disable_notification": silent,
        }
        ok = False
        error = ""
        for attempt in range(self.max_retries + 1):
            try:
                self.client.post_json(f"{API_ROOT}/bot{self.bot_token}/sendMessage", payload)  # type: ignore[union-attr]
                ok = True
                break
            except HttpError as exc:
                error = str(exc)
                if "429" not in error and attempt >= self.max_retries:
                    break
                time.sleep(min(5, 1.5 ** attempt))
        self.sent.append({"ts": time.time(), "event": event, "ok": ok, "error": error})
        if self.database is not None:
            self.database.record_notification({
                "channel": "telegram", "event": event,
                "status": "SENT" if ok else "FAILED", "detail": error or message[:200],
            })
        return ok

    def describe(self) -> dict:
        return {
            "enabled": self.enabled,
            "configured": bool(self.bot_token and self.chat_id),
            "sent": len(self.sent),
            "last_error": next((s["error"] for s in reversed(self.sent) if s["error"]), ""),
        }
