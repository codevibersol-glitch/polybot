"""
core/market_data.py
───────────────────
Market discovery and caching via the Polymarket Gamma API.

Gamma API base: https://gamma-api.polymarket.com
Endpoints used:
  GET /markets          – paginated active-market list
  GET /markets/{id}     – single market detail

Each market record (abbreviated) looks like:
  {
    "id":            "0x...",          # condition id
    "question":      "Will X happen?",
    "slug":          "will-x-happen",
    "active":        true,
    "closed":        false,
    "endDateIso":    "2025-11-05T00:00:00Z",
    "tokens":        [
      {"token_id": "...", "outcome": "Yes", "price": "0.62"},
      {"token_id": "...", "outcome": "No",  "price": "0.38"}
    ],
    "volume":        "123456.78",
    "liquidity":     "45678.00",
    "volume24hr":    "5000.00"
  }

Public API
──────────
  fetch_markets(force=False)  → list[dict]
  get_market_by_id(id)        → dict | None
  get_token_price(token_id)   → float | None   (from CLOB order book)
"""

import threading
import time
from typing import Optional

import requests

from utils.logger import get_logger

log = get_logger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE  = "https://data-api.polymarket.com"

_CACHE_TTL   = 60    # seconds before re-fetching market list
_PAGE_SIZE   = 100   # markets per Gamma API page
_MAX_PAGES   = 1     # fetch one page only – fast startup
_TOP_MARKETS = 20    # keep the N highest-liquidity markets from that page

_session = requests.Session()
_session.headers["User-Agent"] = "PolyBot/1.0"


class MarketDataService:
    """
    Singleton that caches active markets and provides convenience lookups.
    Thread-safe.
    """

    _instance: "MarketDataService | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._markets: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._by_token: dict[str, dict] = {}  # token_id → market
        self._last_fetch: float = 0.0
        self._data_lock = threading.RLock()

    @classmethod
    def instance(cls) -> "MarketDataService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ── Gamma API fetch ───────────────────────────────────────────────────────
    def fetch_markets(self, force: bool = False) -> list[dict]:
        """
        Return the top _TOP_MARKETS active markets sorted by liquidity.
        Results are cached for _CACHE_TTL seconds.
        Pass force=True to bypass the cache.
        """
        with self._data_lock:
            if not force and (time.time() - self._last_fetch) < _CACHE_TTL:
                return list(self._markets)

        log.info("Fetching markets from Gamma API…")
        markets: list[dict] = []
        offset = 0

        for _ in range(_MAX_PAGES):
            try:
                resp = _session.get(
                    f"{GAMMA_BASE}/markets",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": _PAGE_SIZE,
                        "offset": offset,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                page = resp.json()
                # Gamma API returns a list directly or wraps in {"data": [...]}
                if isinstance(page, list):
                    batch = page
                else:
                    batch = page.get("data", page)

                if not batch:
                    break  # no more pages

                markets.extend(batch)
                offset += len(batch)

                if len(batch) < _PAGE_SIZE:
                    break  # last page

            except requests.RequestException as exc:
                log.error("Gamma API error (offset=%d): %s", offset, exc)
                break

        # Sort by liquidity descending → highest-liquidity markets first
        markets.sort(
            key=lambda m: float(m.get("liquidity") or m.get("volume24hr") or 0),
            reverse=True,
        )
        # Filter markets with at least $1k liquidity to avoid illiquid ones without prices
        markets = [m for m in markets if float(m.get("liquidity") or 0) >= 1000]
        markets = markets[:_TOP_MARKETS]

        log.info("Fetched %d active markets (top by liquidity).", len(markets))

        with self._data_lock:
            self._markets = markets
            self._by_id = {m.get("id", m.get("conditionId", "")): m for m in markets}
            # Build token_id → market lookup
            self._by_token = {}
            for m in markets:
                for tok in m.get("tokens", []):
                    if tok.get("token_id"):
                        self._by_token[tok["token_id"]] = m
            self._last_fetch = time.time()

        return list(markets)

    # ── Lookups ───────────────────────────────────────────────────────────────
    def get_market_by_id(self, market_id: str) -> Optional[dict]:
        with self._data_lock:
            return self._by_id.get(market_id)

    def get_market_by_token(self, token_id: str) -> Optional[dict]:
        with self._data_lock:
            return self._by_token.get(token_id)

    def search(self, query: str) -> list[dict]:
        """Case-insensitive substring search on market question / slug."""
        q = query.lower()
        with self._data_lock:
            return [
                m for m in self._markets
                if q in m.get("question", "").lower()
                or q in m.get("slug", "").lower()
            ]

    def all_markets(self) -> list[dict]:
        with self._data_lock:
            return list(self._markets)

    # ── Price helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def midpoint_from_book(book) -> Optional[float]:
        """
        Calculate mid-price from a CLOB order book object.
        Handles both dict items and OrderSummary objects from py_clob_client.
        Returns None if no valid quotes exist.
        """
        try:
            def _price(item) -> float:
                return float(item["price"] if isinstance(item, dict) else item.price)

            def _size(item) -> str:
                return str(item.get("size", "0") if isinstance(item, dict) else item.size)

            bids = [_price(b) for b in (book.bids or []) if _size(b) != "0"]
            asks = [_price(a) for a in (book.asks or []) if _size(a) != "0"]
            if not bids or not asks:
                return None
            return (max(bids) + min(asks)) / 2
        except Exception:
            return None

    @staticmethod
    def implied_prob(token_price: float) -> float:
        """Convert CLOB price (0-1) to implied probability (same scale)."""
        return max(0.0, min(1.0, token_price))

    # ── Copy-trading: top trader history ─────────────────────────────────────
    @staticmethod
    def fetch_user_positions(wallet: str) -> list[dict]:
        """
        Fetch current open positions for a wallet from the Data API.
        Returns only positions with non-zero shares (no resolved-market junk).
        """
        try:
            resp = _session.get(
                f"{DATA_BASE}/positions",
                params={"user": wallet, "limit": 500},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("data", [])
        except Exception as exc:
            log.warning("Could not fetch positions for %s: %s", wallet[:10], exc)
            return []

    @staticmethod
    def fetch_user_closed_positions(wallet: str, limit: int = 500) -> list[dict]:
        """Fetch closed (resolved) positions for realized P/L bootstrapping."""
        try:
            resp = _session.get(
                f"{DATA_BASE}/closed-positions",
                params={"user": wallet, "limit": limit},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("data", [])
        except Exception as exc:
            log.warning("Could not fetch closed positions for %s: %s", wallet[:10], exc)
            return []

    @staticmethod
    def fetch_top_trader_fills(address: str, limit: int = 50) -> list[dict]:
        """
        Fetch recent trades for a Polymarket wallet address via the Data API.
        Returns list of trade dicts with keys: market, side, size, price, timestamp.
        """
        try:
            url = f"{DATA_BASE}/activity"
            resp = _session.get(
                url,
                params={"maker": address, "limit": limit},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("data", [])
        except Exception as exc:
            log.warning("Could not fetch trader fills for %s: %s", address[:10], exc)
            return []

    @staticmethod
    def fetch_top_traders(limit: int = 10) -> list[str]:
        """
        Return a list of top-volume trader wallet addresses via the Data API.
        (Best-effort – returns [] if endpoint unavailable.)
        """
        try:
            resp = _session.get(
                f"{DATA_BASE}/leaderboard",
                params={"limit": limit},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", [])
            return [r.get("address", r.get("maker", "")) for r in rows if r.get("address") or r.get("maker")]
        except Exception as exc:
            log.warning("Could not fetch leaderboard: %s", exc)
            return []
