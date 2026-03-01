"""
strategies/base.py
──────────────────
Abstract base class for all trading strategies.

A strategy is a background-thread worker that:
  1. Receives the current market snapshot on each tick.
  2. Decides whether to place / cancel orders.
  3. Reports its own activity through the shared logger.

To add a new strategy:
  1. Create a new module in strategies/
  2. Subclass BaseStrategy
  3. Implement on_tick() and optionally on_fill()
  4. Register it in core/engine.py

The engine calls on_tick() at a configurable interval.
on_fill() is called immediately on WS fill events.
"""

import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)


class BaseStrategy(ABC):
    """
    Abstract trading strategy.

    Subclasses receive market snapshots and place orders through the
    shared OrderManager.  They never access the CLOB client directly.
    """

    #: Human-readable name shown in the GUI
    name: str = "base"

    def __init__(self, config: dict) -> None:
        """
        config: the strategy-specific config dict from config.json
                e.g. cfg["strategies"]["market_making"]
        """
        self._config = config
        self._enabled = bool(config.get("enabled", False))
        self._lock = threading.Lock()
        self._last_tick: float = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def enable(self) -> None:
        with self._lock:
            self._enabled = True
        log.info("[%s] Enabled.", self.name)

    def disable(self) -> None:
        with self._lock:
            self._enabled = False
        log.info("[%s] Disabled.", self.name)

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def update_config(self, config: dict) -> None:
        """Hot-update strategy parameters from the GUI without restarting."""
        with self._lock:
            self._config = config
            self._enabled = bool(config.get("enabled", False))
        log.info("[%s] Config updated.", self.name)

    # ── Called by the engine on each tick ─────────────────────────────────────
    def tick(self, market_snapshot: "list[dict]", price_map: "dict[str, float]") -> None:
        """
        Wrapper around on_tick that guards with enabled flag and logs timing.

        market_snapshot: list of market dicts with current prices embedded
        price_map:       {token_id → best_midprice}
        """
        if not self.enabled:
            return
        try:
            t0 = time.perf_counter()
            self.on_tick(market_snapshot, price_map)
            elapsed = (time.perf_counter() - t0) * 1000
            if elapsed > 500:
                log.warning("[%s] Slow tick: %.0f ms", self.name, elapsed)
            self._last_tick = time.time()
        except Exception as exc:
            log.error("[%s] Unhandled exception in on_tick: %s", self.name, exc, exc_info=True)

    @abstractmethod
    def on_tick(self, market_snapshot: "list[dict]", price_map: "dict[str, float]") -> None:
        """
        Implement your strategy logic here.

        market_snapshot: list of dicts, each like:
          {
            "market_id": "0x...",
            "question" : "Will X?",
            "tokens"   : [
              {"token_id": "...", "outcome": "Yes", "price": "0.62"},
              {"token_id": "...", "outcome": "No",  "price": "0.38"}
            ],
            "endDateIso": "2025-11-05T00:00:00Z",
            "volume24hr": "5000.00",
          }

        price_map: live mid-prices from CLOB order books
          {token_id: mid_price}
        """

    def on_fill(self, order: dict, fill: dict) -> None:
        """
        Called when one of this strategy's orders is (partially) filled.
        Override to react (e.g., place hedge orders, update inventory).
        """

    # ── Helpers available to all strategies ───────────────────────────────────
    def cfg(self, key: str, default=None):
        """Thread-safe config value access."""
        with self._lock:
            return self._config.get(key, default)

    @staticmethod
    def token_for_outcome(market: dict, outcome: str) -> Optional[str]:
        """Return the token_id for 'Yes' or 'No' in a market dict."""
        for tok in market.get("tokens", []):
            if tok.get("outcome", "").lower() == outcome.lower():
                return tok.get("token_id")
        return None

    @staticmethod
    def best_bid_ask(book) -> "tuple[float, float]":
        """
        Extract best bid and ask from a CLOB order book object.
        Returns (best_bid, best_ask) or (0.0, 1.0) if book is empty.
        """
        try:
            bids = sorted(
                [float(b["price"]) for b in book.bids if float(b.get("size", 0)) > 0],
                reverse=True,
            )
            asks = sorted(
                [float(a["price"]) for a in book.asks if float(a.get("size", 0)) > 0],
            )
            best_bid = bids[0] if bids else 0.01
            best_ask = asks[0] if asks else 0.99
            return best_bid, best_ask
        except Exception:
            return 0.01, 0.99
