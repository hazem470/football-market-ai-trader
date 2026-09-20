"""Anthropic Messages API provider (optional AI layer)."""
from __future__ import annotations

from dataclasses import dataclass

from src.ai.base import (
    SYSTEM_PROMPT,
    AiError,
    AiExtraction,
    AiProvider,
    parse_model_json,
    validate_extraction,
)
from src.core.http import HttpClient, HttpError


@dataclass
class AnthropicProvider(AiProvider):
    client: HttpClient
    api_key: str = ""
    base_url: str = "https://api.anthropic.com/v1"
    model: str = "claude-3-5-haiku-latest"
    timeout_seconds: float = 45.0
    name: str = "anthropic"

    def available(self) -> bool:
        return bool(self.api_key)

    def extract_facts(self, texts: list[str], context: dict) -> AiExtraction:
        if not self.api_key:
            raise AiError("no AI API key configured")
        snippet_block = "\n---\n".join(texts[:8])
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0.0,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Match: {context.get('home_team', '')} vs {context.get('away_team', '')}\n"
                        f"Snippets:\n{snippet_block}"
                    ),
                }
            ],
        }
        try:
            response = self.client.post_json(
                f"{self.base_url.rstrip('/')}/messages",
                payload,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        except HttpError as exc:
            raise AiError(f"Anthropic error: {exc}") from exc
        try:
            blocks = response["content"]
            content = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
        except (KeyError, TypeError) as exc:
            raise AiError(f"unexpected Anthropic response: {str(response)[:200]}") from exc
        parsed = parse_model_json(content)
        facts, rejected = validate_extraction(parsed)
        return AiExtraction(facts=facts, provider=self.name, model=self.model, raw=content[:2000],
                            rejected_reason=rejected, accepted=not rejected)
