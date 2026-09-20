"""Live Polymarket execution via the official Python CLOB client.

STOP AND READ:
  * This module is only reachable when TRADING_MODE=live AND
    POLYMARKET_ALLOW_LIVE_TRADING=true AND the CLI confirmation phrase matches.
  * The client package (`py-clob-client`) is an OPTIONAL dependency
    (requirements-live.txt). Verify the current package and API surface against
    https://docs.polymarket.com before live use - Polymarket has been migrating
    from `py-clob-client` / `py-clob-client-v2` to a unified `polymarket-client`
    SDK, and this module must be re-checked against the docs you are on.
  * Nothing in this project stores, prints or transmits a seed phrase. Only a
    dedicated trading wallet private key, read from .env or the OS keyring.

Order submission is deliberately NOT retried automatically: a retry of a
financial order can duplicate a fill. Retries require reconciliation via the
client order id.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from src.execution.base import ExecutionVenue, OrderRequest, OrderResult, OrderStatus
from src.wallet.keystore import WalletConfig


class LiveExecutionDisabled(RuntimeError):
    """Raised when live execution is attempted without explicit enablement."""


class LiveExecutionUnavailable(RuntimeError):
    """Raised when the optional CLOB client package or credentials are missing."""


@dataclass
class PolymarketExecutor(ExecutionVenue):
    """Real order placement. Disabled unless every live gate is satisfied."""

    wallet: WalletConfig
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    allow_live: bool = False
    #: The official SDK client, injected/created in `initialise()`.
    client: object | None = None
    name: str = "polymarket"
    is_live: bool = True
    _initialised: bool = field(default=False, init=False)
    _init_error: str = field(default="", init=False)

    # ------------------------------------------------------------------ setup
    def _require_gates(self) -> None:
        if not self.allow_live:
            raise LiveExecutionDisabled(
                "LIVE TRADING IS DISABLED. Set POLYMARKET_ALLOW_LIVE_TRADING=true and run "
                "`python -m app.cli live --confirm \"I UNDERSTAND THE RISK\"`."
            )
        if not self.wallet.has_signer():
            raise LiveExecutionUnavailable(
                "no dedicated trading-wallet key configured (POLYMARKET_PRIVATE_KEY). "
                "This project never accepts a seed phrase."
            )

    def initialise(self, derive_api_creds: bool = True) -> None:
        """Build the official CLOB client. Never logs or echoes credentials."""
        self._require_gates()
        for_creds = self.wallet.has_api_creds()
        try:
            from py_clob_client.client import ClobClient  # type: ignore
            try:
                from py_clob_client.clob_types import ApiCreds  # type: ignore
            except ImportError:  # older/newer layout
                from py_clob_client.clob_types import ApiCreds  # type: ignore
        except ImportError as exc:
            self._init_error = (
                "py-clob-client is not installed. Live trading needs it: "
                "`pip install -r requirements-live.txt`. Verify the current package against "
                "https://docs.polymarket.com/getting-started/python before use."
            )
            raise LiveExecutionUnavailable(self._init_error) from exc

        kwargs: dict = {
            "host": self.clob_host,
            "key": self.wallet.private_key,
            "chain_id": self.wallet.chain_id,
        }
        if self.wallet.signature_type:
            kwargs["signature_type"] = self.wallet.signature_type
        if self.wallet.funder_address:
            kwargs["funder"] = self.wallet.funder_address

        self.client = ClobClient(**kwargs)
        if not for_creds and derive_api_creds:
            creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(creds)
            self.wallet.api_key = getattr(creds, "api_key", "")
            self.wallet.api_secret = getattr(creds, "api_secret", "")
            self.wallet.api_passphrase = getattr(creds, "api_passphrase", "")
        elif for_creds:
            self.client.set_api_creds(ApiCreds(
                api_key=self.wallet.api_key,
                api_secret=self.wallet.api_secret,
                api_passphrase=self.wallet.api_passphrase,
            ))
        self._initialised = True

    # ---------------------------------------------------------------- preflight
    def preflight(self) -> tuple[bool, list[str]]:
        problems: list[str] = []
        try:
            self._require_gates()
        except (LiveExecutionDisabled, LiveExecutionUnavailable) as exc:
            return False, [str(exc)]
        if self.wallet.chain_id not in (137, 80002):
            problems.append(f"unexpected chain id {self.wallet.chain_id} (expected 137 Polygon)")
        if not self._initialised:
            try:
                self.initialise()
            except (LiveExecutionUnavailable, LiveExecutionDisabled) as exc:
                problems.append(str(exc))
            except Exception as exc:
                problems.append(f"client init failed: {exc}")
        if self._initialised:
            try:
                ok = self.client.get_ok() if hasattr(self.client, "get_ok") else True
                if not ok:
                    problems.append("CLOB /ok endpoint did not return OK")
            except Exception as exc:
                problems.append(f"CLOB health probe failed: {exc}")
        return (not problems), problems

    def balance(self) -> float | None:
        """USDC available for trading, if the client can report it."""
        if not self._initialised or self.client is None:
            return None
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams  # type: ignore

            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            data = self.client.get_balance_allowance(params)
            return float(data.get("balance", 0)) / 1e6 if isinstance(data, dict) else None
        except Exception:
            return None

    # ------------------------------------------------------------------ trading
    def submit(self, request: OrderRequest) -> OrderResult:
        if not self._initialised or self.client is None:
            return OrderResult(order_id="", status=OrderStatus.REJECTED,
                               message="live client not initialised (call preflight first)")
        from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
        from py_clob_client.order_builder.constants import BUY, SELL  # type: ignore

        side = BUY if request.side.upper() == "BUY" else SELL
        order_type = getattr(OrderType, request.order_type.upper(), OrderType.GTC)
        try:
            args = OrderArgs(
                price=request.price,
                size=request.size,
                side=side,
                token_id=request.token_id,
            )
            signed = self.client.create_order(args)
            response = self.client.post_order(signed, order_type)
        except Exception as exc:
            # NO automatic retry here on purpose.
            return OrderResult(order_id="", status=OrderStatus.REJECTED,
                               message=f"order submission failed: {exc}",
                               raw={"error": str(exc)})

        exchange_id = str(response.get("orderID") or response.get("orderId") or "")
        status_raw = str(response.get("status") or "").lower()
        if response.get("success") is False or response.get("errorMsg"):
            return OrderResult(order_id=f"LIV-{uuid.uuid4().hex[:10]}", status=OrderStatus.REJECTED,
                               message=str(response.get("errorMsg") or response.get("error") or "rejected"),
                               exchange_order_id=exchange_id, raw=response)
        status = {
            "matched": OrderStatus.FILLED,
            "live": OrderStatus.OPEN,
            "delayed": OrderStatus.OPEN,
            "unmatched": OrderStatus.OPEN,
        }.get(status_raw, OrderStatus.OPEN)
        result = OrderResult(
            order_id=f"LIV-{uuid.uuid4().hex[:10]}",
            status=status,
            exchange_order_id=exchange_id,
            message=f"submitted (exchange status: {status_raw or 'unknown'})",
            raw=response,
        )
        if status is OrderStatus.FILLED:
            result.filled_size = request.size
            result.avg_price = request.price
        return result

    def cancel(self, order_id: str) -> bool:
        if not self._initialised or self.client is None:
            return False
        try:
            self.client.cancel(order_id)
            return True
        except Exception:
            return False

    def open_orders(self, market_id: str | None = None) -> list[dict]:
        if not self._initialised or self.client is None:
            return []
        try:
            orders = self.client.get_orders() or []
        except Exception:
            return []
        out = []
        for order in orders:
            entry = {
                "order_id": order.get("id"),
                "market": order.get("market"),
                "asset_id": order.get("asset_id"),
                "price": order.get("price"),
                "size": order.get("original_size"),
                "status": order.get("status"),
            }
            if market_id and order.get("market") != market_id:
                continue
            out.append(entry)
        return out

    def reconcile(self, client_order_id: str) -> dict:
        """Look a submission up after a timeout instead of blindly retrying."""
        if not self._initialised or self.client is None:
            return {"found": False, "reason": "client not initialised"}
        try:
            orders = self.client.get_orders() or []
        except Exception as exc:
            return {"found": False, "reason": f"lookup failed: {exc}"}
        for order in orders:
            if str(order.get("client_order_id") or "") == client_order_id:
                return {"found": True, "order": order}
        return {"found": False, "reason": "not present in open orders; may have filled or failed",
                "checked_at": time.time()}

    def describe(self) -> dict:
        return {
            "name": self.name, "live": self.is_live,
            "initialised": self._initialised, "init_error": self._init_error,
            "wallet": self.wallet.redacted(),
        }
