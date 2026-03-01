"""
core/client.py
──────────────
Thread-safe wrapper around py-clob-client's ClobClient.

Features
  • Singleton – one client instance shared across all threads
  • Retry decorator – exponential back-off on transient HTTP failures
  • Rate-limit awareness – respects 429 responses with adaptive delay
  • Key never logged – private key stripped before any log output
  • derive_api_key() called lazily so L2 operations work automatically

Typical usage
─────────────
    from core.client import PolyClient
    pc = PolyClient.instance()
    # after connect():
    book = pc.get_order_book(token_id)
"""

import threading
import time
import functools
from typing import Any, Callable, TypeVar

from utils.logger import get_logger

log = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ── Retry config ──────────────────────────────────────────────────────────────
_MAX_RETRIES = 5
_BASE_DELAY  = 1.0   # seconds
_MAX_DELAY   = 30.0  # seconds cap


def _retry(fn: F) -> F:
    """
    Decorator: retry up to _MAX_RETRIES times with exponential back-off.
    Re-raises on permanent failures (e.g. auth errors).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        delay = _BASE_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                msg = str(exc)
                # Don't retry permanent errors – they won't self-heal
                if any(kw in msg.lower() for kw in ("401", "403", "invalid key")):
                    log.error("%s: auth error – not retrying: %s", fn.__name__, exc)
                    raise
                # 404 = token/market doesn't exist – retrying won't help
                if "404" in msg or "no orderbook" in msg.lower():
                    log.debug("%s: 404/no orderbook – skipping: %s", fn.__name__, exc)
                    raise
                if attempt == _MAX_RETRIES:
                    log.error("%s: giving up after %d attempts: %s", fn.__name__, attempt, exc)
                    raise
                # Rate-limit → use longer delay
                if "429" in msg or "rate limit" in msg.lower():
                    delay = min(delay * 3, _MAX_DELAY)
                else:
                    delay = min(delay * 2, _MAX_DELAY)
                log.warning(
                    "%s: attempt %d/%d failed (%s) – retry in %.1fs",
                    fn.__name__, attempt, _MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
    return wrapper  # type: ignore[return-value]


# ── Singleton CLOB client ──────────────────────────────────────────────────────
class PolyClient:
    """
    Thread-safe singleton wrapping py_clob_client.ClobClient.

    Call PolyClient.connect(private_key, wallet, sig_type) once from the
    Setup tab.  Everywhere else call PolyClient.instance().
    """

    _instance: "PolyClient | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._client = None          # underlying ClobClient
        self._api_creds = None       # L2 API credentials
        self._connected = False
        self._call_lock = threading.Lock()  # serialise API calls

    # ── Singleton access ──────────────────────────────────────────────────────
    @classmethod
    def instance(cls) -> "PolyClient":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ── Connection ────────────────────────────────────────────────────────────
    def connect(self, private_key: str, wallet: str, sig_type: int = 0) -> None:
        """
        Initialise ClobClient and derive L2 API credentials.
        Raises on failure so the GUI can surface the error.
        """
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON

        log.info("Connecting to Polymarket CLOB (sig_type=%d)…", sig_type)

        with self._call_lock:
            self._client = ClobClient(
                host="https://clob.polymarket.com",
                chain_id=POLYGON,
                key=private_key,
                signature_type=sig_type,
                funder=wallet,
            )
            # Register / derive deterministic L2 creds from the private key
            self._api_creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(self._api_creds)
            self._connected = True

        log.info("Connected.  API key: %s…", self._api_creds.api_key[:8])

    def connect_with_api_creds(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        wallet: str,
    ) -> None:
        """
        Connect using pre-existing L2 API credentials exported from Polymarket.
        api_secret is a base64url-encoded 32-byte L2 private key, used for both
        EIP-712 order signing and HMAC HTTP authentication (signature_type=2).
        """
        import base64
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON
        from py_clob_client.clob_types import ApiCreds

        log.info("Connecting with pre-existing L2 API credentials…")

        # api_secret is the base64url-encoded L2 signing private key
        padding = "=" * (-len(api_secret) % 4)
        decoded = base64.urlsafe_b64decode(api_secret + padding)
        hex_key = "0x" + decoded.hex()

        with self._call_lock:
            self._client = ClobClient(
                host="https://clob.polymarket.com",
                chain_id=POLYGON,
                key=hex_key,        # L2 private key for EIP-712 order signing
                signature_type=2,   # Polymarket Magic / L2 key auth
                funder=wallet,
            )
            # Set provided creds directly — skip derive_api_key()
            self._api_creds = ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
            self._client.set_api_creds(self._api_creds)
            self._connected = True

        log.info("Connected via API creds.  API key: %s…", api_key[:8])

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def api_key(self) -> "str | None":
        return self._api_creds.api_key if self._api_creds else None

    @property
    def api_secret(self) -> "str | None":
        return self._api_creds.api_secret if self._api_creds else None

    @property
    def api_passphrase(self) -> "str | None":
        return self._api_creds.api_passphrase if self._api_creds else None

    def _require(self):
        if not self._connected or self._client is None:
            raise RuntimeError("Not connected – call connect() first.")

    # ── Market data ───────────────────────────────────────────────────────────
    @_retry
    def get_markets(self, next_cursor: str = "") -> dict:
        """Return one page of CLOB markets."""
        self._require()
        with self._call_lock:
            return self._client.get_markets(next_cursor=next_cursor)

    @_retry
    def get_order_book(self, token_id: str) -> Any:
        """Return live order book for a token."""
        self._require()
        with self._call_lock:
            return self._client.get_order_book(token_id)

    @_retry
    def get_last_trade_price(self, token_id: str) -> "float | None":
        """Return the most recent trade price for a token (0-1 scale)."""
        self._require()
        with self._call_lock:
            try:
                resp = self._client.get_last_trade_price(token_id)
                return float(resp.get("price", 0)) if isinstance(resp, dict) else float(resp)
            except Exception:
                return None

    # ── Order management ──────────────────────────────────────────────────────
    @_retry
    def create_and_post_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,          # "BUY" or "SELL"
        order_type: str = "GTC",   # "GTC" | "FOK" | "GTD"
    ) -> dict:
        """
        Sign and post a limit order.  Returns response dict with 'orderID'.
        side: "BUY" or "SELL"
        """
        # In py-clob-client >=0.20, Side was moved to order_builder.constants
        # and side is passed as a plain string "BUY" / "SELL"
        from py_clob_client.clob_types import OrderArgs, OrderType

        self._require()
        _side  = side.upper()   # "BUY" or "SELL" – plain string
        _otype = order_type.upper()  # "GTC" | "FOK" | "GTD"

        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 4),
            size=round(size, 2),
            side=_side,
        )

        log.info(
            "Placing %s %s order: token=…%s price=%.4f size=%.2f",
            side, order_type, token_id[-8:], price, size,
        )

        with self._call_lock:
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed, _otype)

        log.info("Order posted – id: %s", resp.get("orderID", "?"))
        return resp

    @_retry
    def cancel_order(self, order_id: str) -> dict:
        """Cancel a specific open order."""
        self._require()
        log.info("Cancelling order %s…", order_id)
        with self._call_lock:
            return self._client.cancel(order_id=order_id)

    @_retry
    def cancel_all(self) -> dict:
        """Cancel ALL open orders across all markets."""
        self._require()
        log.warning("Cancelling ALL open orders!")
        with self._call_lock:
            return self._client.cancel_all()

    @_retry
    def get_open_orders(self) -> list:
        """Return list of all open orders."""
        self._require()
        with self._call_lock:
            resp = self._client.get_orders()
            return resp if isinstance(resp, list) else resp.get("data", [])

    @_retry
    def get_trades(self, limit: int = 200) -> list:
        """Return recent fill history."""
        self._require()
        with self._call_lock:
            resp = self._client.get_trades()
            data = resp if isinstance(resp, list) else resp.get("data", [])
            return data[:limit]

    # ── Balance ───────────────────────────────────────────────────────────────
    def get_usdc_balance(self) -> float:
        """
        Return the wallet's USDC cash balance in dollars.
        The CLOB returns raw amounts in 6-decimal units (USDC on Polygon).
        $4.80 → raw 4_800_000 → returned as 4.8
        """
        self._require()
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            with self._call_lock:
                resp = self._client.get_balance_allowance(
                    params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                )
            raw = resp.get("balance", "0") if isinstance(resp, dict) else "0"
            return float(raw) / 1_000_000
        except Exception as exc:
            log.debug("get_usdc_balance failed: %s", exc)
            return 0.0

    def get_usdc_wallet_balance(self) -> float:
        """
        Return the wallet's actual USDC balance on Polygon in dollars.
        Queries the USDC contract directly via web3.
        """
        try:
            from web3 import Web3
            w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
            usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
            contract = w3.eth.contract(address=usdc_contract, abi=abi)
            balance = contract.functions.balanceOf(self._client.funder).call()
            return float(balance) / 1_000_000  # USDC has 6 decimals
        except Exception as exc:
            log.debug("get_usdc_wallet_balance failed: %s", exc)
            return 0.0

    # ── Allowances ────────────────────────────────────────────────────────────
    def check_allowances(self) -> dict:
        """
        Return dict with 'usdc' and 'ctoken' allowance amounts.
        Returns empty dict if unavailable.
        """
        self._require()
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            with self._call_lock:
                usdc = self._client.get_balance_allowance(
                    params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                )
                ct = self._client.get_balance_allowance(
                    params=BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL_TOKEN)
                )
            return {"usdc": usdc, "ctoken": ct}
        except Exception as exc:
            log.error("check_allowances failed: %s", exc)
            return {}

    @_retry
    def set_allowances(self) -> None:
        """Approve USDC and Conditional Tokens for the CLOB exchange contracts."""
        self._require()
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        log.info("Setting USDC allowance…")
        with self._call_lock:
            self._client.update_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
        log.info("Setting Conditional Token allowance…")
        with self._call_lock:
            self._client.update_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL_TOKEN)
            )
        log.info("Allowances set successfully.")
