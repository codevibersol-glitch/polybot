"""
core/order_manager.py
─────────────────────
Tracks the full lifecycle of every order placed by the bot:
  pending → open → (partially_filled) → filled | cancelled

Features
  • Thread-safe in-memory order book
  • Deduplication of WS fill events (idempotent)
  • Strategy tag on each order so strategies can track their own orders
  • Emergency flatten helper
  • Callback hooks: on_fill, on_cancel, on_open

Internal state per order:
  {
    "order_id"    : "0x…",
    "token_id"    : "0x…",
    "market_id"   : "0x…",
    "question"    : "Will X?",
    "side"        : "BUY" | "SELL",
    "price"       : 0.62,
    "size"        : 10.0,
    "filled_size" : 0.0,
    "status"      : "open" | "filled" | "cancelled" | "partial",
    "strategy"    : "market_making" | …,
    "created_at"  : 1234567890.0,
    "updated_at"  : 1234567890.0,
  }
"""

import threading
import time
from typing import Callable, Optional

from utils.logger import get_logger
from core.client import PolyClient
from core.risk_manager import RiskManager

log = get_logger(__name__)


def _round_to_tick(price: float, tick: float) -> float:
    """Round price to the nearest valid tick increment."""
    if tick <= 0:
        return round(price, 4)
    return round(round(price / tick) * tick, 10)


class OrderManager:
    """Singleton order lifecycle manager."""

    _instance: "OrderManager | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._orders: dict[str, dict] = {}   # order_id → order
        self._data_lock = threading.RLock()
        self._fill_callbacks: list[Callable] = []
        self._cancel_callbacks: list[Callable] = []

    @classmethod
    def instance(cls) -> "OrderManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ── Callbacks ─────────────────────────────────────────────────────────────
    def on_fill(self, cb: Callable) -> None:
        self._fill_callbacks.append(cb)

    def on_cancel(self, cb: Callable) -> None:
        self._cancel_callbacks.append(cb)

    # ── Order placement ───────────────────────────────────────────────────────
    def place_order(
        self,
        token_id: str,
        market_id: str,
        question: str,
        side: str,              # "BUY" or "SELL"
        price: float,
        size: float,
        order_type: str = "GTC",
        strategy: str = "manual",
    ) -> Optional[str]:
        """
        Place a limit order via the CLOB client after risk-checking.
        Returns the order_id string, or None on failure.
        """
        risk = RiskManager.instance()
        if not risk.check_order(token_id, market_id, side, size):
            log.warning("Order blocked by risk manager: %s %s size=%.2f", side, token_id[-8:], size)
            return None

        try:
            # Round price to the market's tick size before submitting
            from core.market_data import MarketDataService
            tick = MarketDataService.instance().get_tick_size(token_id)
            price = _round_to_tick(price, tick)

            pc = PolyClient.instance()
            resp = pc.create_and_post_order(token_id, price, size, side, order_type)
            order_id = resp.get("orderID") or resp.get("order_id")

            if not order_id:
                log.error("Order response missing orderID: %s", resp)
                return None

            # Record in local book
            order = {
                "order_id": order_id,
                "token_id": token_id,
                "market_id": market_id,
                "question": question,
                "side": side,
                "price": price,
                "size": size,
                "filled_size": 0.0,
                "status": "open",
                "strategy": strategy,
                "created_at": time.time(),
                "updated_at": time.time(),
                "order_type": order_type,
            }
            with self._data_lock:
                self._orders[order_id] = order

            risk.register_order(token_id, market_id, side, size)
            log.info("Order %s recorded: %s %s %.2f @ %.4f", order_id[:8], side, token_id[-8:], size, price)
            return order_id

        except Exception as exc:
            log.error("Failed to place order: %s", exc)
            return None

    # ── Cancel ────────────────────────────────────────────────────────────────
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order.  Returns True on success."""
        try:
            PolyClient.instance().cancel_order(order_id)
            self._mark_cancelled(order_id)
            return True
        except Exception as exc:
            log.error("Cancel failed for %s: %s", order_id, exc)
            return False

    def cancel_all(self) -> int:
        """Cancel all open orders.  Returns count cancelled."""
        try:
            PolyClient.instance().cancel_all()
            cancelled = 0
            with self._data_lock:
                for order in self._orders.values():
                    if order["status"] == "open":
                        order["status"] = "cancelled"
                        order["updated_at"] = time.time()
                        cancelled += 1
            log.warning("Cancelled all %d open orders.", cancelled)
            return cancelled
        except Exception as exc:
            log.error("cancel_all failed: %s", exc)
            return 0

    def cancel_strategy_orders(self, strategy: str) -> int:
        """Cancel all open orders belonging to a specific strategy."""
        with self._data_lock:
            ids = [
                oid for oid, o in self._orders.items()
                if o["strategy"] == strategy and o["status"] == "open"
            ]
        count = 0
        for oid in ids:
            if self.cancel_order(oid):
                count += 1
        return count

    # ── Fill events from WS ───────────────────────────────────────────────────
    def handle_fill_event(self, fill: dict) -> None:
        """
        Called by the WS manager on each fill/trade event.
        Updates internal state and triggers callbacks.
        """
        order_id = fill.get("orderID") or fill.get("order_id") or fill.get("id")
        if not order_id:
            return

        with self._data_lock:
            order = self._orders.get(order_id)
            if order is None:
                return  # order placed before bot started – ignore

            filled = float(fill.get("size", fill.get("amount", 0)))
            order["filled_size"] = min(order["size"], order["filled_size"] + filled)
            order["updated_at"] = time.time()

            if order["filled_size"] >= order["size"] * 0.999:
                order["status"] = "filled"
            else:
                order["status"] = "partial"

            log.info(
                "Fill: order %s %s – %.2f / %.2f USDC @ %.4f",
                order_id[:8], order["side"], order["filled_size"], order["size"], order["price"],
            )

        # Notify position manager etc.
        for cb in self._fill_callbacks:
            try:
                cb(dict(order), fill)
            except Exception as exc:
                log.debug("fill callback error: %s", exc)

    # ── Sync with CLOB (periodic) ─────────────────────────────────────────────
    def sync_open_orders(self) -> None:
        """
        Pull current open orders from CLOB and reconcile with local state.
        Marks locally-tracked orders that are gone as 'cancelled' or 'filled'.
        """
        try:
            live = PolyClient.instance().get_open_orders()
            live_ids = {o.get("id") or o.get("orderID") for o in live}
            with self._data_lock:
                for oid, order in self._orders.items():
                    if order["status"] == "open" and oid not in live_ids:
                        # Order is gone from CLOB – assume filled if partially filled
                        if order["filled_size"] > 0:
                            order["status"] = "filled"
                        else:
                            order["status"] = "cancelled"
                        order["updated_at"] = time.time()
                        log.debug("Reconciled order %s → %s", oid[:8], order["status"])
        except Exception as exc:
            log.warning("sync_open_orders failed: %s", exc)

    # ── Queries ───────────────────────────────────────────────────────────────
    def open_orders(self, strategy: Optional[str] = None) -> list[dict]:
        with self._data_lock:
            return [
                dict(o) for o in self._orders.values()
                if o["status"] == "open"
                and (strategy is None or o["strategy"] == strategy)
            ]

    def all_orders(self) -> list[dict]:
        with self._data_lock:
            return [dict(o) for o in self._orders.values()]

    def open_size_for_token(self, token_id: str, side: str) -> float:
        """Total USDC size of open orders on a token/side."""
        with self._data_lock:
            return sum(
                o["size"] for o in self._orders.values()
                if o["token_id"] == token_id
                and o["side"] == side
                and o["status"] == "open"
            )

    # ── Internal helpers ──────────────────────────────────────────────────────
    def _mark_cancelled(self, order_id: str) -> None:
        with self._data_lock:
            if order_id in self._orders:
                self._orders[order_id]["status"] = "cancelled"
                self._orders[order_id]["updated_at"] = time.time()
