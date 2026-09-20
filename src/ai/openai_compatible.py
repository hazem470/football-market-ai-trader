"""OpenAI-compatible chat provider (OpenAI, OpenRouter, local servers)."""
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
class OpenAiCompatibleProvider(AiProvider):
    client: HttpClient
    api_key: str = ""
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "openai/gpt-4o-mini"
    timeout_seconds: float = 45.0
    name: str = "openai_compatible"

    def available(self) -> bool:
        return bool(self.api_key)

    def extract_facts(self, texts: list[str], context: dict) -> AiExtraction:
        if not self.api_key:
            raise AiError("no AI API key configured")
        snippet_block = "\n---\n".join(texts[:8])
        user_prompt = (
            f"Match: {context.get('home_team', '')} vs {context.get('away_team', '')}\n"
            f"Date: {context.get('match_date', '')}\n"
            f"Known players: {', '.join(context.get('players', [])[:30])}\n\n"
            f"Snippets:\n{snippet_block}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self.client.post_json(
                f"{self.base_url.rstrip('/')}/chat/completions",
                payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except HttpError as exc:
            raise AiError(f"AI provider error: {exc}") from exc

        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiError(f"unexpected AI response shape: {str(response)[:200]}") from exc

        parsed = parse_model_json(content)
        facts, rejected = validate_extraction(parsed)
        return AiExtraction(
            facts=facts, provider=self.name, model=self.model, raw=content[:2000],
            rejected_reason=rejected, accepted=not rejected,
        )
