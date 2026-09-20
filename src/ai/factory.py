"""AI provider factory + a no-op provider that keeps the engine honest."""
from __future__ import annotations

from dataclasses import dataclass

from src.ai.anthropic import AnthropicProvider
from src.ai.base import AiExtraction, AiProvider
from src.ai.openai_compatible import OpenAiCompatibleProvider
from src.config.settings import Settings
from src.core.http import HttpClient


@dataclass
class NullAiProvider(AiProvider):
    """Used when the AI layer is disabled. Returns nothing - never fabricates."""

    name: str = "disabled"

    def available(self) -> bool:
        return False

    def extract_facts(self, texts: list[str], context: dict) -> AiExtraction:
        return AiExtraction(provider=self.name, model="", facts=[], accepted=False,
                            rejected_reason="AI layer disabled by configuration")


def build_ai_provider(settings: Settings, client: HttpClient) -> AiProvider:
    section = settings.section("ai")
    if not section.get("enabled") or not settings.secrets.has_ai():
        return NullAiProvider()
    provider_name = str(section.get("provider", "openrouter")).lower()
    common = dict(
        client=client,
        api_key=settings.secrets.ai_api_key,
        base_url=str(section.get("base_url", "https://openrouter.ai/api/v1")),
        model=str(section.get("model", "openai/gpt-4o-mini")),
        timeout_seconds=float(section.get("timeout_seconds", 45)),
    )
    if provider_name == "anthropic":
        return AnthropicProvider(**common)  # type: ignore[arg-type]
    provider = OpenAiCompatibleProvider(**common)  # type: ignore[arg-type]
    provider.name = provider_name
    return provider
