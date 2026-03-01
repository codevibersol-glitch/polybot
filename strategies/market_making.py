"""
strategies/market_making.py
───────────────────────────
Dynamic Market-Making Strategy

Logic summary
─────────────
  1. For each "auto-trade" market, pull the live CLOB order book.
  2. Calculate the mid-price from best bid/ask.
  3. Compute a spread that widens with:
       • Distance to expiry (wider → closer to expiry)
       • Estimated volatility proxy (recent book-move)
       • Lower liquidity score
  4. Post a BUY limit at (mid − half_spread) and
         a SELL limit at (mid + half_spread).
  5. Cancel and re-post if:
       • Existing quotes are stale (> refresh_sec old)
       • Mid-price moved by more than 0.5%
  6. Enforce inventory limits: if net long > max_inventory_usd, widen
     the ask and tighten the bid (lean the book toward reversion).

Config keys (from config.json → strategies.market_making):
  spread_pct     : float   base spread as fraction of price (e.g. 0.02 = 2%)
  max_pos_usd    : float   max USDC in a single market's open MM orders
  refresh_sec    : int     how often to re-quote (seconds)
  max_markets    : int     how many markets to MM simultaneously
"""

import time
from typing import Optional

from strategies.base import BaseStrategy
from core.client import PolyClient
from core.order_manager import OrderManager
from core.position_manager import PositionManager
from core.market_data import MarketDataService
from utils.logger import get_logger

log = get_logger(__name__)

_STRATEGY = "market_making"
_MIN_SPREAD = 0.01    # floor: never quote tighter than 1 cent
_MAX_SPREAD = 0.20    # ceiling: never quote wider than 20 cents
_PRICE_MOVE_THRESHOLD = 0.005   # 0.5% move triggers re-quote


class MarketMakingStrategy(BaseStrategy):
    """
    Provides two-sided quotes around the order-book midpoint.

    Extend / modify by overriding:
      _compute_spread()  – plug in your own vol / regime model
      _select_markets()  – custom market filter (e.g. min liquidity)
    """

    name = "market_making"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        # Track our current quotes: market_id → {buy_id, sell_id, mid, posted_at}
        self._quotes: dict[str, dict] = {}

    # ── Core tick ─────────────────────────────────────────────────────────────
    def on_tick(self, market_snapshot: list, price_map: dict) -> None:
        """Re-quote all watched markets if needed."""
        markets = self._select_markets(market_snapshot)
        max_markets = int(self.cfg("max_markets", 5))

        for market in markets[:max_markets]:
            try:
                self._manage_quotes(market, price_map)
            except Exception as exc:
                log.error("[MM] Error managing quotes for %s: %s", market.get("question", "?")[:40], exc)

    # ── Quote management per market ───────────────────────────────────────────
    def _manage_quotes(self, market: dict, price_map: dict) -> None:
        market_id = market.get("id") or market.get("conditionId", "")
        if not market_id:
            return

        # Get Yes token (we MM on the Yes token for simplicity)
        yes_token = self.token_for_outcome(market, "Yes")
        if not yes_token:
            return

        # Fetch live order book
        try:
            book = PolyClient.instance().get_order_book(yes_token)
        except Exception:
            return

        best_bid, best_ask = self.best_bid_ask(book)
        mid = (best_bid + best_ask) / 2

        if mid <= 0.01 or mid >= 0.99:
            return   # avoid degenerate markets

        refresh_sec = float(self.cfg("refresh_sec", 10))
        existing   = self._quotes.get(market_id, {})
        old_mid    = existing.get("mid", 0.0)
        posted_at  = existing.get("posted_at", 0.0)

        # Decision: should we re-quote?
        price_moved = abs(mid - old_mid) > _PRICE_MOVE_THRESHOLD * mid if old_mid else True
        stale       = (time.time() - posted_at) > refresh_sec

        if not (price_moved or stale):
            return

        # Cancel old quotes if any
        if existing.get("buy_id"):
            OrderManager.instance().cancel_order(existing["buy_id"])
        if existing.get("sell_id"):
            OrderManager.instance().cancel_order(existing["sell_id"])

        spread      = self._compute_spread(market, best_bid, best_ask, mid)
        half_spread = spread / 2

        buy_price  = round(max(0.01, mid - half_spread), 4)
        sell_price = round(min(0.99, mid + half_spread), 4)

        max_pos    = float(self.cfg("max_pos_usd", 50.0))
        order_size = round(max_pos / 4, 2)   # split into 4 tiers; use ¼ each side

        # Inventory lean: if we're long, push sell order closer to mid
        shares = PositionManager.instance().shares_held(yes_token)
        cost   = PositionManager.instance().cost_basis(yes_token)
        if cost > max_pos * 0.6:
            # We're quite long – tighten buy, widen sell to encourage reversion
            sell_price = round(min(0.99, mid + half_spread * 0.7), 4)
            order_size = round(order_size * 0.5, 2)   # reduce new buys

        question  = market.get("question", "?")[:60]

        # Place new quotes
        buy_id = OrderManager.instance().place_order(
            token_id=yes_token,
            market_id=market_id,
            question=question,
            side="BUY",
            price=buy_price,
            size=order_size,
            strategy=_STRATEGY,
        )
        sell_id = OrderManager.instance().place_order(
            token_id=yes_token,
            market_id=market_id,
            question=question,
            side="SELL",
            price=sell_price,
            size=order_size,
            strategy=_STRATEGY,
        )

        self._quotes[market_id] = {
            "buy_id":    buy_id,
            "sell_id":   sell_id,
            "mid":       mid,
            "posted_at": time.time(),
        }

        log.debug(
            "[MM] %s  bid=%.4f ask=%.4f  spread=%.4f",
            question[:30], buy_price, sell_price, spread,
        )

    # ── Spread calculation (override to extend) ───────────────────────────────
    def _compute_spread(
        self,
        market: dict,
        best_bid: float,
        best_ask: float,
        mid: float,
    ) -> float:
        """
        Calculate bid-ask spread.

        Extension points:
          • Add implied-volatility model here
          • Use volume24hr as liquidity proxy
          • Widen on news events ("aggressive mode")
        """
        base_spread = float(self.cfg("spread_pct", 0.02))

        # Liquidity adjustment: widen if low volume
        try:
            vol_24h = float(market.get("volume24hr", market.get("volume", 0)) or 0)
        except (ValueError, TypeError):
            vol_24h = 0.0

        if vol_24h < 500:
            liquidity_factor = 2.0
        elif vol_24h < 5_000:
            liquidity_factor = 1.5
        elif vol_24h < 50_000:
            liquidity_factor = 1.2
        else:
            liquidity_factor = 1.0

        # Time-to-expiry adjustment: widen as expiry approaches
        import dateutil.parser as dp
        import datetime
        expiry_factor = 1.0
        try:
            end_str = market.get("endDateIso") or market.get("end_date_iso", "")
            if end_str:
                end_dt  = dp.parse(end_str)
                if end_dt.tzinfo:
                    from datetime import timezone
                    now_dt = datetime.datetime.now(timezone.utc)
                else:
                    now_dt = datetime.datetime.utcnow()
                hours   = (end_dt - now_dt).total_seconds() / 3600
                if hours < 6:
                    expiry_factor = 3.0
                elif hours < 24:
                    expiry_factor = 2.0
                elif hours < 72:
                    expiry_factor = 1.4
        except Exception:
            pass

        # Mid-price proximity: widen near 50% (maximum uncertainty)
        uncertainty = 1.0 + 0.5 * (1.0 - abs(mid - 0.5) / 0.5)

        spread = base_spread * liquidity_factor * expiry_factor * uncertainty
        return max(_MIN_SPREAD, min(_MAX_SPREAD, spread))

    # ── Market selection ───────────────────────────────────────────────────────
    def _select_markets(self, snapshot: list) -> list:
        """
        Filter snapshot to markets this strategy should trade.
        Default: only markets explicitly added to auto_trade_markets.
        Override to add volume / liquidity filters.
        """
        from core.engine import BotEngine  # lazy import to avoid circular
        auto = BotEngine.instance().auto_trade_markets
        return [m for m in snapshot if (m.get("id") or m.get("conditionId", "")) in auto]

    # ── Fill reaction ─────────────────────────────────────────────────────────
    def on_fill(self, order: dict, fill: dict) -> None:
        """Immediately re-quote the side that just filled."""
        market_id = order.get("market_id", "")
        if market_id in self._quotes:
            # Mark mid as stale so next tick re-quotes immediately
            self._quotes[market_id]["posted_at"] = 0.0
