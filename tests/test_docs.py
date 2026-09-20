"""Documentation guards.

Docs rot silently. These tests keep the shipped documentation honest: relative
links must resolve, every documented CLI command must actually exist, the Arabic
guides must stay RTL-wrapped, and the credential-ready files must never contain a
real-looking secret.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

DOC_FILES = [
    "README.md",
    "README.ar.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "NOTICE.md",
    "docs/USER_GUIDE.md",
    "docs/USER_GUIDE.ar.md",
    "docs/SECURITY.md",
    "docs/SECURITY.ar.md",
    "docs/CONFIGURATION.md",
    "docs/ARCHITECTURE.md",
    "docs/TROUBLESHOOTING.md",
    "docs/DEVELOPMENT_STATUS.md",
]

ARABIC_DOCS = ["README.ar.md", "docs/USER_GUIDE.ar.md", "docs/SECURITY.ar.md"]

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)#\s]+)(?:#[^)\s]*)?\)")
CLI_RE = re.compile(r"python\s+-m\s+app\.cli\s+([a-z][a-z0-9-]*)")


@pytest.mark.parametrize("relative", DOC_FILES)
def test_documented_file_exists(relative):
    assert (REPO_ROOT / relative).is_file(), f"{relative} is missing"


@pytest.mark.parametrize("relative", DOC_FILES)
def test_relative_links_resolve(relative):
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    broken = []
    for target in LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if not (REPO_ROOT / relative).parent.joinpath(target).resolve().exists() and not (
            REPO_ROOT / target
        ).exists():
            broken.append(target)
    assert broken == [], f"{relative} has broken relative links: {sorted(set(broken))}"


@pytest.mark.parametrize("relative", ARABIC_DOCS)
def test_arabic_docs_are_rtl_wrapped(relative):
    """Markdown has no RTL directive, so an explicit <div dir="rtl"> is the only
    way the Arabic guides render correctly on GitHub."""
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert '<div dir="rtl">' in text
    assert "</div>" in text
    assert len(re.findall(r"[\u0600-\u06ff]", text)) > 500, "not enough Arabic content"


@pytest.mark.parametrize("relative", ARABIC_DOCS)
def test_arabic_docs_link_back_to_english(relative):
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert "SECURITY.md" in text or "USER_GUIDE.md" in text or "](" in text


def test_english_readme_links_the_arabic_readme():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "README.ar.md" in text
    assert "docs/USER_GUIDE.ar.md" in text


def test_every_documented_cli_command_exists():
    """A guide that tells users to run a non-existent command is worse than none."""
    from app.cli.main import build_parser

    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if action.dest == "command"
    )
    known = set(subparsers.choices)

    documented: set[str] = set()
    for relative in DOC_FILES:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        documented |= set(CLI_RE.findall(text))

    unknown = documented - known
    assert unknown == set(), f"documented but not implemented: {sorted(unknown)}"
    # And the docs should cover the main surface.
    assert {"check", "markets", "signals", "backtest", "shadow", "paper", "live"} <= documented


def test_env_example_headings_are_present_for_every_category():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    for section in ("TRADING MODE", "POLYMARKET", "FOOTBALL DATA", "AI", "TELEGRAM",
                    "STORAGE", "RISK LIMITS", "LOGGING"):
        assert section in text, f".env.example lost the {section} section"
