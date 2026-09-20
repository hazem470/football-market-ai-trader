"""Optional AI layer.

Hard boundary: the LLM extracts STRUCTURED FACTS from unstructured text. It can
never emit a trading decision, a size or a price, and it cannot bypass the risk
engine. Any provider output that contains forbidden fields is rejected outright.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

FORBIDDEN_OUTPUT_FIELDS = frozenset({"decision", "side", "size", "price", "order", "buy", "sell"})


class AiError(RuntimeError):
    """Provider unavailable or returned unusable output."""


@dataclass
class NewsFact:
    """One validated structured fact extracted from free text."""

    player: str = ""
    team: str = ""
    fact_type: str = ""      # INJURY | SUSPENSION | LINEUP | FORM | TRANSFER | OTHER
    detail: str = ""
    confidence: float = 0.0
    source_text: str = ""
    validated: bool = False

    def as_dict(self) -> dict:
        return {
            "player": self.player, "team": self.team, "fact_type": self.fact_type,
            "detail": self.detail, "confidence": round(self.confidence, 3),
            "validated": self.validated,
        }


@dataclass
class AiExtraction:
    facts: list[NewsFact] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    raw: str = ""
    rejected_reason: str = ""
    accepted: bool = False

    def as_dict(self) -> dict:
        return {
            "provider": self.provider, "model": self.model,
            "facts": [f.as_dict() for f in self.facts],
            "accepted": self.accepted, "rejected_reason": self.rejected_reason,
        }


def validate_extraction(payload: dict, max_items: int = 8) -> tuple[list[NewsFact], str]:
    """Reject any payload that tries to smuggle a trading decision."""
    if not isinstance(payload, dict):
        return [], "payload is not a JSON object"
    lowered_keys = {str(k).lower() for k in payload}
    if lowered_keys & FORBIDDEN_OUTPUT_FIELDS:
        return [], f"payload contains forbidden fields: {sorted(lowered_keys & FORBIDDEN_OUTPUT_FIELDS)}"
    raw_facts = payload.get("facts")
    if not isinstance(raw_facts, list):
        return [], "payload has no 'facts' list"
    facts: list[NewsFact] = []
    allowed_types = {"INJURY", "SUSPENSION", "LINEUP", "FORM", "TRANSFER", "AVAILABILITY", "OTHER"}
    for item in raw_facts[:max_items]:
        if not isinstance(item, dict):
            continue
        fact_type = str(item.get("fact_type", "OTHER")).upper()
        if fact_type not in allowed_types:
            fact_type = "OTHER"
        facts.append(NewsFact(
            player=str(item.get("player", ""))[:80],
            team=str(item.get("team", ""))[:80],
            fact_type=fact_type,
            detail=str(item.get("detail", ""))[:400],
            confidence=max(0.0, min(1.0, float(item.get("confidence", 0.0) or 0.0))),
            source_text=str(item.get("source_text", ""))[:400],
            validated=False,
        ))
    return facts, ""


class AiProvider(ABC):
    name = "ai"

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def extract_facts(self, texts: list[str], context: dict) -> AiExtraction: ...


SYSTEM_PROMPT = (
    "You extract structured football availability facts from news snippets.\n"
    "Return ONLY JSON of the form "
    '{"facts":[{"player":"","team":"","fact_type":"INJURY|SUSPENSION|LINEUP|FORM|OTHER",'
    '"detail":"","confidence":0.0,"source_text":""}]}.\n'
    "Rules: never output a betting decision, stake, size, side or price. "
    "Never invent a fact that is not explicitly stated in the snippets. "
    "If a snippet is ambiguous, omit it. Confidence is your uncertainty about the "
    "extraction, not about the football outcome."
)


def parse_model_json(text: str) -> dict:
    """Tolerantly parse a JSON object out of a model response."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AiError("no JSON object found in model output")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AiError(f"invalid JSON from model: {exc}") from exc
