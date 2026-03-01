"""
core/position_manager.py
────────────────────────
Builds a real-time position book from fill events and CLOB trade history.

A "position" aggregates all fills for a given (market_id, token_id, outcome):
  {
    "market_id"       : "0x…",
    "token_id"        : "0x…",
    "outcome"         : "Yes",
    "question"        : "Will X?",
    "shares"          : 12.5,       # net shares held  (positive = long)
    "avg_entry_price" : 0.63,       # USDC per share (0-1)
    "cost_basis"      : 7.875,      # total USDC spent
    "current_price"   : 0.71,       # from live order book
    "market_value"    : 8.875,      # shares × current_price
    "unrealized_pnl"  : 1.0,        # market_value − cost_basis
    "realized_pnl"    : 0.0,        # locked-in profit from partial closes
    "expiry"          : "2025-11-05T00:00:00Z",
    "last_updated"    : 1234567890.0,
  }

Public API
──────────
  pm = PositionManager.instance()
  pm.on_fill(order, fill)          ← called by OrderManager
  pm.update_prices(token_prices)   ← dict[token_id → float]
  pm.positions()                   → list[dict]
  pm.portfolio_value()             → float  (total market value in USDC)
  pm.total_unrealized_pnl()        → float
  pm.export_csv(path)              → None
"""

import csv
import threading
import time
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)


class PositionManager:
    """Singleton position tracker."""

    _instance: "PositionManager | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        # key: (market_id, token_id)
        self._positions: dict[tuple, dict] = {}
        self._realized_pnl: float = 0.0
        self._data_lock = threading.RLock()

    @classmethod
    def instance(cls) -> "PositionManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ── Fill ingestion ────────────────────────────────────────────────────────
    def on_fill(self, order: dict, fill: dict) -> None:
        """
        Update position when an order is (partially) filled.
        Called by OrderManager's fill callback.
        """
        token_id  = order["token_id"]
        market_id = order["market_id"]
        side      = order["side"]       # "BUY" or "SELL"
        price     = float(fill.get("price", order["price"]))
        size      = float(fill.get("size", fill.get("amount", 0)))  # USDC
        shares    = size / price if price > 0 else 0.0

        key = (market_id, token_id)

        with self._data_lock:
            if key not in self._positions:
                self._positions[key] = {
                    "market_id":       market_id,
                    "token_id":        token_id,
                    "outcome":         self._guess_outcome(order),
                    "question":        order.get("question", ""),
                    "shares":          0.0,
                    "avg_entry_price": 0.0,
                    "cost_basis":      0.0,
                    "current_price":   price,
                    "market_value":    0.0,
                    "unrealized_pnl":  0.0,
                    "realized_pnl":    0.0,
                    "expiry":          "",
                    "last_updated":    time.time(),
                }

            pos = self._positions[key]

            if side == "BUY":
                # Increase position – update cost basis (FIFO average)
                old_cost  = pos["cost_basis"]
                pos["cost_basis"]      = old_cost + size
                pos["shares"]         += shares
                if pos["shares"] > 0:
                    pos["avg_entry_price"] = pos["cost_basis"] / pos["shares"]

            else:  # SELL
                # Decrease position – realise P&L
                realised = shares * (price - pos["avg_entry_price"])
                pos["realized_pnl"] += realised
                self._realized_pnl  += realised
                pos["shares"]        = max(0.0, pos["shares"] - shares)
                pos["cost_basis"]    = pos["shares"] * pos["avg_entry_price"]

            pos["current_price"] = price
            pos["market_value"]  = pos["shares"] * price
            pos["unrealized_pnl"] = pos["market_value"] - pos["cost_basis"]
            pos["last_updated"]   = time.time()

        log.debug(
            "Position updated: %s %s shares=%.3f avg=%.4f",
            token_id[-8:], side, self._positions[key]["shares"], self._positions[key]["avg_entry_price"],
        )

    # ── Price updates ─────────────────────────────────────────────────────────
    def update_prices(self, token_prices: "dict[str, float]") -> None:
        """
        Batch-update current market prices and recalculate unrealized P/L.
        Call from the engine's periodic price poll.
        token_prices: {token_id → current_price}
        """
        with self._data_lock:
            for key, pos in self._positions.items():
                token_id = pos["token_id"]
                if token_id in token_prices:
                    p = token_prices[token_id]
                    pos["current_price"]  = p
                    pos["market_value"]   = pos["shares"] * p
                    pos["unrealized_pnl"] = pos["market_value"] - pos["cost_basis"]
                    pos["last_updated"]   = time.time()

    def update_expiry(self, token_id: str, expiry: str) -> None:
        with self._data_lock:
            for pos in self._positions.values():
                if pos["token_id"] == token_id:
                    pos["expiry"] = expiry

    # ── Aggregates ────────────────────────────────────────────────────────────
    def positions(self) -> list[dict]:
        """Return all non-zero positions as list of dicts."""
        with self._data_lock:
            return [
                dict(p) for p in self._positions.values()
                if p["shares"] > 0.001
            ]

    def portfolio_value(self) -> float:
        with self._data_lock:
            return sum(p["market_value"] for p in self._positions.values())

    def total_unrealized_pnl(self) -> float:
        with self._data_lock:
            return sum(p["unrealized_pnl"] for p in self._positions.values())

    def total_realized_pnl(self) -> float:
        with self._data_lock:
            return self._realized_pnl

    def total_pnl(self) -> float:
        return self.total_realized_pnl() + self.total_unrealized_pnl()

    def shares_held(self, token_id: str) -> float:
        with self._data_lock:
            for pos in self._positions.values():
                if pos["token_id"] == token_id:
                    return pos["shares"]
        return 0.0

    def cost_basis(self, token_id: str) -> float:
        with self._data_lock:
            for pos in self._positions.values():
                if pos["token_id"] == token_id:
                    return pos["cost_basis"]
        return 0.0

    # ── CSV export ────────────────────────────────────────────────────────────
    def export_csv(self, path: Optional[str] = None) -> str:
        """Export positions to CSV.  Returns the file path used."""
        if path is None:
            path = str(Path.home() / f"polybot_positions_{int(time.time())}.csv")

        fieldnames = [
            "question", "outcome", "shares", "avg_entry_price",
            "current_price", "cost_basis", "market_value",
            "unrealized_pnl", "realized_pnl", "expiry",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.positions())

        log.info("Positions exported to %s", path)
        return path

    # ── Load from Data API /positions ─────────────────────────────────────────
    def load_from_positions_api(self, positions: list[dict]) -> None:
        """
        Bootstrap from the Polymarket Data API /positions endpoint.
        This is the preferred bootstrap path: it returns only currently-open
        positions with accurate share counts and entry prices, so resolved
        markets are automatically excluded.
        """
        loaded = 0
        with self._data_lock:
            for pos in positions:
                token_id  = pos.get("asset") or pos.get("asset_id") or pos.get("token_id") or ""
                market_id = pos.get("conditionId") or pos.get("condition_id") or pos.get("market_id") or ""
                outcome   = pos.get("outcome") or pos.get("outcomeLabel") or ""
                question  = pos.get("title") or pos.get("question") or ""
                size      = float(pos.get("size", 0) or 0)          # shares held
                avg_price = float(pos.get("avgPrice", 0) or 0)      # USDC per share
                cost      = float(pos.get("initialValue", 0) or 0)  # total cost basis
                cur_val   = float(pos.get("currentValue", 0) or 0)  # live market value
                upnl      = float(pos.get("cashPnl", 0) or 0)       # unrealized P/L
                rpnl      = float(pos.get("realizedPnl", 0) or 0)
                end_date  = pos.get("endDate") or pos.get("expiry") or ""

                if not token_id or size <= 0:
                    continue

                if cost == 0 and avg_price > 0:
                    cost = size * avg_price
                if cur_val == 0 and avg_price > 0:
                    cur_val = size * avg_price

                key = (market_id, token_id)
                self._positions[key] = {
                    "market_id":       market_id,
                    "token_id":        token_id,
                    "outcome":         outcome,
                    "question":        question,
                    "shares":          size,
                    "avg_entry_price": avg_price,
                    "cost_basis":      cost,
                    "current_price":   avg_price,
                    "market_value":    cur_val,
                    "unrealized_pnl":  upnl,
                    "realized_pnl":    rpnl,
                    "expiry":          end_date,
                    "last_updated":    time.time(),
                }
                self._realized_pnl += rpnl
                loaded += 1

        log.info("Bootstrapped %d open positions from Data API.", loaded)

    def load_from_closed_positions_api(self, closed: list[dict]) -> None:
        """
        Add realized P/L from the /closed-positions endpoint.
        Called after load_from_positions_api to accumulate historical gains/losses.
        """
        total_rpnl = 0.0
        for pos in closed:
            rpnl = float(pos.get("realizedPnl", pos.get("cashPnl", 0)) or 0)
            total_rpnl += rpnl

        with self._data_lock:
            self._realized_pnl += total_rpnl

        log.info(
            "Loaded realized P/L from %d closed positions: %+.2f USDC",
            len(closed), total_rpnl,
        )

    # ── Load history from CLOB trades ─────────────────────────────────────────
    def load_from_trades(self, trades: list[dict]) -> None:
        """
        Bootstrap positions from historical trade list.
        Trades should be in chronological order (oldest first).
        """
        # Sort oldest first
        sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", t.get("created_at", 0)))

        for trade in sorted_trades:
            # Normalise field names across API versions
            token_id  = trade.get("asset_id") or trade.get("token_id") or ""
            market_id = trade.get("market") or trade.get("market_id") or ""
            side      = (trade.get("side") or "BUY").upper()
            price        = float(trade.get("price", 0) or 0)
            # Polymarket's /data/trades returns "size" in SHARES (conditional
            # tokens exchanged), not USDC.  on_fill() expects size in USDC,
            # so convert: usdc_size = shares × price_per_share.
            size_shares  = float(trade.get("size", trade.get("amount", 0)) or 0)
            size         = size_shares * price   # USDC amount

            if not token_id or price == 0 or size == 0:
                continue

            fake_order = {
                "token_id": token_id,
                "market_id": market_id,
                "side": side,
                "price": price,
                "size": size,
                "question": trade.get("question", ""),
            }
            fake_fill = {"price": price, "size": size}
            self.on_fill(fake_order, fake_fill)

        log.info("Loaded %d trades into position manager.", len(sorted_trades))

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _guess_outcome(order: dict) -> str:
        """Guess Yes/No from order side (BUY = buying Yes shares)."""
        return "Yes" if order.get("side") == "BUY" else "No"
