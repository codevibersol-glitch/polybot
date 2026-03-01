"""
strategies/copy_trading.py
──────────────────────────
Copy-Trading-Lite Strategy

Logic summary
─────────────
  1. Periodically fetch the public leaderboard from the Polymarket Data API.
  2. Pull the most recent trade history for each top trader.
  3. For each unique trade not yet mirrored:
       • Scale the notional size by `scale_factor` (e.g. 0.10 = 10% of their size).
       • Apply risk checks (max_pos_usd, risk manager).
       • Place the same-direction order (BUY/SELL) on the same token.
  4. Keep a seen-set to avoid duplicating fills across ticks.

Config keys:
  scale_factor         : float   fraction of their trade size to copy (0.10 = 10%)
  max_pos_usd          : float   hard cap per market for this strategy
  check_interval_min   : int     how often to poll (minutes)

NOTE: This strategy uses public on-chain trade data only.
      No information advantage is exploited beyond public order flow.
"""

import hashlib
import time
from typing import Optional

from strategies.base import BaseStrategy
from core.order_manager import OrderManager
from core.market_data import MarketDataService
from core.position_manager import PositionManager
from utils.logger import get_logger

log = get_logger(__name__)

_STRATEGY = "copy_trading"


class CopyTradingStrategy(BaseStrategy):
    """
    Mirrors trades of the top-volume Polymarket traders at a scaled size.

    To extend: override _select_traders() to use a curated list of
    known profitable addresses instead of the leaderboard.
    """

    name = "copy_trading"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._seen_fills: set[str] = set()   # hash of (trader, token, side, ts)
        self._last_check: float    = 0.0
        self._trader_cache: list[str] = []
        self._trader_cache_ts: float  = 0.0

    # ── Tick ──────────────────────────────────────────────────────────────────
    def on_tick(self, market_snapshot: list, price_map: dict) -> None:
        interval = float(self.cfg("check_interval_min", 5)) * 60
        if time.time() - self._last_check < interval:
            return
        self._last_check = time.time()

        traders = self._select_traders()
        if not traders:
            log.debug("[CT] No traders found – skipping.")
            return

        mds = MarketDataService.instance()
        scale = float(self.cfg("scale_factor", 0.10))
        max_p = float(self.cfg("max_pos_usd", 20.0))

        for trader in traders[:10]:   # limit to top 10
            fills = mds.fetch_top_trader_fills(trader, limit=20)
            for fill in fills:
                self._maybe_copy(fill, scale, max_p, mds)

    # ── Per-fill evaluation ───────────────────────────────────────────────────
    def _maybe_copy(
        self,
        fill: dict,
        scale: float,
        max_pos: float,
        mds: MarketDataService,
    ) -> None:
        """Decide whether to mirror a specific fill."""
        # Build deduplication hash
        key_parts = (
            fill.get("maker", fill.get("address", "")),
            fill.get("asset_id", fill.get("token_id", "")),
            (fill.get("side") or "").upper(),
            str(fill.get("timestamp", fill.get("created_at", ""))),
        )
        fill_hash = hashlib.sha256("|".join(str(p) for p in key_parts).encode()).hexdigest()[:16]

        if fill_hash in self._seen_fills:
            return
        self._seen_fills.add(fill_hash)

        token_id  = fill.get("asset_id") or fill.get("token_id", "")
        side      = (fill.get("side") or "BUY").upper()
        orig_size = float(fill.get("size", fill.get("amount", 0)) or 0)
        price     = float(fill.get("price", 0) or 0)

        if not token_id or orig_size <= 0 or price <= 0:
            return

        # Do not copy SELLs unless we hold the position
        if side == "SELL":
            shares = PositionManager.instance().shares_held(token_id)
            if shares < 0.1:
                return   # we don't hold it – skip

        # Scale down the size
        our_size = round(orig_size * scale, 2)
        if our_size < 1.0:
            return   # too small to bother

        our_size = min(our_size, max_pos)

        # Resolve market_id for this token
        market  = mds.get_market_by_token(token_id)
        if not market:
            return   # unknown token
        market_id = market.get("id") or market.get("conditionId", "")
        question  = market.get("question", "?")[:60]

        # Check existing position cap
        cost = PositionManager.instance().cost_basis(token_id)
        if cost + our_size > max_pos:
            return

        log.info(
            "[CT] Copying %s %.2f USDC on …%s (scaled from %.2f)",
            side, our_size, token_id[-8:], orig_size,
        )

        OrderManager.instance().place_order(
            token_id=token_id,
            market_id=market_id,
            question=question,
            side=side,
            price=round(price, 4),
            size=our_size,
            strategy=_STRATEGY,
        )

    # ── Trader selection (override to customise) ───────────────────────────────
    def _select_traders(self) -> list[str]:
        """
        Return list of trader addresses to follow.

        Default: refreshes leaderboard every 30 min.
        Override to use a static curated list:
          return ["0xABC...", "0xDEF..."]
        """
        if time.time() - self._trader_cache_ts < 1800:   # 30 min cache
            return self._trader_cache

        traders = MarketDataService.fetch_top_traders(limit=10)
        if traders:
            self._trader_cache    = traders
            self._trader_cache_ts = time.time()
            log.info("[CT] Refreshed top-trader list: %d addresses.", len(traders))

        return self._trader_cache
