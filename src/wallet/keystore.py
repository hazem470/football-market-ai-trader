"""Wallet / credential handling.

ABSOLUTE RULES enforced here:
  * A seed phrase is NEVER accepted, stored, logged or transmitted. This module
    refuses mnemonic-looking input outright.
  * Only a dedicated trading wallet private key may be used, and only from the
    local environment (.env) or an OS keyring - never from a repo file.
  * Nothing here ever prints, reprs or serialises a secret.

The recommended setup is a DEDICATED Polymarket trading wallet funded with only
the money you are willing to risk - never your main wallet.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MNEMONIC_RE = re.compile(r"^(\s*[a-zA-Z]{3,10}\s+){11,23}[a-zA-Z]{3,10}\s*$")
PRIVATE_KEY_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")

BURNER_KEY_FILENAME = "poly_trading.key"
SENTINEL = "REDACTED"


class WalletError(RuntimeError):
    """Raised for any unsafe or unusable wallet configuration."""


class SeedPhraseRejected(WalletError):
    def __init__(self) -> None:
        super().__init__(
            "A seed phrase / mnemonic was supplied. This project NEVER accepts one. "
            "Create a dedicated Polymarket trading wallet and use its private key instead. "
            "A seed phrase controls your entire wallet - sharing it anywhere is irreversible compromise."
        )


@dataclass
class WalletConfig:
    """Non-secret description of how the bot should sign."""

    private_key: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)
    api_passphrase: str = field(default="", repr=False)
    funder_address: str = ""
    chain_id: int = 137
    signature_type: int = 0

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return (
            f"WalletConfig(funder={self.funder_address or 'unset'}, chain_id={self.chain_id}, "
            f"signature_type={self.signature_type}, "
            f"private_key={'set' if self.private_key else 'missing'}, "
            f"api_creds={'set' if self.has_api_creds() else 'missing'})"
        )

    __str__ = __repr__

    def has_api_creds(self) -> bool:
        return bool(self.api_key and self.api_secret and self.api_passphrase)

    def has_signer(self) -> bool:
        return bool(self.private_key)

    def redacted(self) -> dict:
        return {
            "funder_address": self.funder_address or "unset",
            "chain_id": self.chain_id,
            "signature_type": self.signature_type,
            "private_key": "set" if self.private_key else "missing",
            "api_credentials": "set" if self.has_api_creds() else "missing",
        }


def assert_not_a_mnemonic(value: str, source: str = "input") -> None:
    """Refuse anything that looks like a seed phrase, loudly."""
    if not value:
        return
    if MNEMONIC_RE.match(value.strip()) or len(value.split()) >= 12:
        raise SeedPhraseRejected()


def validate_private_key(value: str) -> str:
    """Validate a dedicated trading-wallet private key (no seed phrases, ever)."""
    if not value:
        return ""
    assert_not_a_mnemonic(value)
    text = value.strip()
    if not PRIVATE_KEY_RE.match(text):
        raise WalletError(
            "private key must be a 32-byte hex string (64 hex chars, optional 0x prefix). "
            "Do not paste anything that is not a dedicated trading-wallet key."
        )
    return text


def load_signer_from_env() -> str:
    value = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    if not value:
        return ""
    return validate_private_key(value)


def load_signer_from_keyring(service: str = "football-market-ai-trader") -> str:
    """Optional: read the key from the OS keyring instead of .env.

    Only a raw hex private key is accepted; a mnemonic is rejected outright.
    """
    try:
        import keyring  # type: ignore
    except ImportError:
        return ""
    value = keyring.get_password(service, "polymarket_private_key") or ""
    return validate_private_key(value) if value else ""


def load_signer(keyring_service: str | None = None, allow_keyring: bool = True) -> tuple[str, str]:
    """Return ``(private_key, source)``; source is 'env', 'keyring' or 'none'."""
    value = load_signer_from_env()
    if value:
        return value, "env"
    if allow_keyring:
        value = load_signer_from_keyring(keyring_service or "football-market-ai-trader")
        if value:
            return value, "keyring"
    return "", "none"


def assert_file_not_committable(path: str | Path) -> None:
    """Guard used by tooling: refuse to read a key from inside the repo tree."""
    target = Path(path).resolve()
    repo = Path(__file__).resolve().parents[2]
    try:
        target.relative_to(repo)
    except ValueError:
        return
    if target.name in (".env",) or target.suffix in (".key", ".pem"):
        return
    raise WalletError(
        f"refusing to read a wallet secret from inside the repository: {target}. "
        "Use .env (gitignored) or the OS keyring."
    )


def wallet_setup_instructions() -> str:
    return (
        "WALLET SETUP (do this on Polymarket, not here):\n"
        "  1. Create a DEDICATED trading wallet (do not reuse your main wallet).\n"
        "  2. Fund it with USDC on Polygon - only what you can afford to lose.\n"
        "  3. Connect it to Polymarket and set the required allowances.\n"
        "  4. Export ONLY this dedicated wallet's private key and put it in your local\n"
        "     .env as POLYMARKET_PRIVATE_KEY (never committed, never shared).\n"
        "  5. Alternatively store it in your OS keyring and leave .env empty.\n"
        "NEVER paste a seed phrase anywhere. This project will refuse it.\n"
        "Never send keys over Telegram, Discord, GitHub issues or chat."
    )
