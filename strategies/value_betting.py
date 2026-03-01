"""
strategies/value_betting.py
────────────────────────────
Probability-Edge / Value-Betting Strategy

Logic summary
─────────────
  For each watched market:
    1. Calculate implied probability from CLOB mid-price.
    2. Compare to user-supplied "fair probability" OR a simple
       heuristic model (time-decay bias: No value rises pre-expiry).
    3. If |implied − fair| > min_edge_pct threshold:
         • BUY Yes  if implied_prob < fair_prob − edge
         • BUY No   if implied_prob > fair_prob + edge  (i.e. Yes is overpriced)
    4. Size via Kelly fraction of available bankroll.
    5. Track open positions; do NOT add more if already at max_pos_usd.

Config keys:
  min_edge_pct   : float   minimum edge to act on (e.g. 0.05 = 5%)
  max_pos_usd    : float   max USDC per market
  kelly_fraction : float   fraction of full Kelly (0.25 = "quarter Kelly")

Extending this strategy
───────────────────────
  • Override _fair_probability() to plug in an ML model, news feed, or
    user-defined fair values (edit per-market in the GUI Strategies tab).
  • Add correlation checks: if market A implies market B should be 0.7
    but B is 0.5, trade B.
"""

import time
from typing import Optional

from strategies.base import BaseStrategy
from core.client import PolyClient
from core.order_manager import OrderManager
from core.position_manager import PositionManager
from core.risk_manager import RiskManager
from utils.logger import get_logger

log = get_logger(__name__)

_STRATEGY = "value_betting"
_COOLDOWN  = 120   # seconds to wait before re-entering the same side


class ValueBettingStrategy(BaseStrategy):
    """
    Systematic value / probability-arbitrage betting.

    Fair values are stored in _fair_values dict:
      { token_id → float (0-1) }

    Users can update these from the GUI Strategies tab.
    If no fair value is set, the strategy falls back to the
    time-decay heuristic (No becomes more valuable near expiry).
    """

    name = "value_betting"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._fair_values: dict[str, float] = {}   # token_id → fair prob
        self._last_trade: dict[str, float] = {}     # token_id → timestamp

    # ── Main tick ─────────────────────────────────────────────────────────────
    def on_tick(self, market_snapshot: list, price_map: dict) -> None:
        from core.engine import BotEngine  # lazy import
        watched = BotEngine.instance().watched_markets
        auto    = BotEngine.instance().auto_trade_markets

        for market in market_snapshot:
            mid = market.get("id") or market.get("conditionId", "")
            if mid not in watched and mid not in auto:
                continue
            try:
                self._evaluate_market(market, price_map)
            except Exception as exc:
                log.error("[VB] Error in market %s: %s", market.get("question", "?")[:30], exc)

    # ── Per-market evaluation ─────────────────────────────────────────────────
    def _evaluate_market(self, market: dict, price_map: dict) -> None:
        yes_token = self.token_for_outcome(market, "Yes")
        no_token  = self.token_for_outcome(market, "No")

        if not yes_token:
            return

        # Live implied probability for Yes
        implied_prob = price_map.get(yes_token)
        if implied_prob is None:
            # Fall back to Gamma API price
            try:
                implied_prob = float(
                    next(
                        t["price"] for t in market.get("tokens", [])
                        if t.get("outcome") == "Yes"
                    )
                )
            except (StopIteration, ValueError, KeyError):
                return

        if implied_prob <= 0.01 or implied_prob >= 0.99:
            return   # near-resolved market – skip

        fair_prob = self._fair_probability(market, yes_token, implied_prob)
        if fair_prob is None:
            return

        edge      = fair_prob - implied_prob
        min_edge  = float(self.cfg("min_edge_pct", 0.05))
        max_pos   = float(self.cfg("max_pos_usd", 30.0))
        kf        = float(self.cfg("kelly_fraction", 0.25))

        question  = market.get("question", "?")[:60]
        market_id = market.get("id") or market.get("conditionId", "")

        # ── Overpriced No (buy Yes) ───────────────────────────────────────────
        if edge > min_edge:
            # Check cooldown
            if time.time() - self._last_trade.get(yes_token + "BUY", 0) < _COOLDOWN:
                return
            # Check existing position
            cost = PositionManager.instance().cost_basis(yes_token)
            if cost >= max_pos:
                log.debug("[VB] Already at max position for Yes on %s", question[:30])
                return

            # Kelly size
            rm   = RiskManager.instance()
            avail = max(0.0, max_pos - cost)
            size  = rm.kelly_size(
                edge=edge,
                price=implied_prob,
                kelly_fraction=kf,
                bankroll=avail,
                max_usd=max_pos,
            )
            if size < 1.0:
                return

            log.info("[VB] Edge=%.2f%% BUY Yes on %s @ %.4f", edge*100, question[:40], implied_prob)
            oid = OrderManager.instance().place_order(
                token_id=yes_token,
                market_id=market_id,
                question=question,
                side="BUY",
                price=round(implied_prob + 0.01, 4),  # slightly aggressive limit
                size=size,
                strategy=_STRATEGY,
            )
            if oid:
                self._last_trade[yes_token + "BUY"] = time.time()

        # ── Overpriced Yes (buy No = sell Yes) ───────────────────────────────
        elif edge < -min_edge and no_token:
            if time.time() - self._last_trade.get(no_token + "BUY", 0) < _COOLDOWN:
                return
            no_price    = 1.0 - implied_prob   # No price = 1 − Yes price
            fair_no     = 1.0 - fair_prob
            no_edge     = fair_no - no_price

            cost = PositionManager.instance().cost_basis(no_token)
            if cost >= max_pos:
                return

            rm   = RiskManager.instance()
            avail = max(0.0, max_pos - cost)
            size  = rm.kelly_size(
                edge=no_edge,
                price=no_price,
                kelly_fraction=kf,
                bankroll=avail,
                max_usd=max_pos,
            )
            if size < 1.0:
                return

            log.info("[VB] Edge=%.2f%% BUY No on %s @ %.4f", no_edge*100, question[:40], no_price)
            oid = OrderManager.instance().place_order(
                token_id=no_token,
                market_id=market_id,
                question=question,
                side="BUY",
                price=round(no_price + 0.01, 4),
                size=size,
                strategy=_STRATEGY,
            )
            if oid:
                self._last_trade[no_token + "BUY"] = time.time()

    # ── Fair value model (override to extend) ─────────────────────────────────
    def _fair_probability(
        self,
        market: dict,
        yes_token: str,
        implied_prob: float,
    ) -> Optional[float]:
        """
        Return our estimate of the true probability for the Yes token.

        Default heuristic:
          • Use user-set fair value if available.
          • Otherwise apply a simple time-decay bias:
              – If implied < 0.15 and >24h to expiry: slight upward nudge
                (markets often under-price unlikely events early on)
              – If implied > 0.85 and <24h to expiry: slight downward nudge
                (resolution risk premium)
          • Returns None to skip the market.

        Extension: connect to a news API, ML model, or external data source
                   and return a data-driven probability here.
        """
        # User-set fair value takes priority
        if yes_token in self._fair_values:
            return self._fair_values[yes_token]

        # Time-decay heuristic
        import dateutil.parser as dp
        import datetime as _dt
        end_str = market.get("endDateIso") or market.get("end_date_iso", "")
        hours_left: Optional[float] = None
        if end_str:
            try:
                end_dt = dp.parse(end_str)
                if end_dt.tzinfo:
                    now_dt = _dt.datetime.now(_dt.timezone.utc)
                else:
                    now_dt = _dt.datetime.utcnow()
                hours_left = max(0.0, (end_dt - now_dt).total_seconds() / 3600)
            except Exception:
                pass

        # Simple bias adjustments (conservative – only act on extremes)
        if hours_left is not None:
            if implied_prob < 0.10 and hours_left > 48:
                # Slight upward revision for deeply-unlikely events with time
                return implied_prob + 0.06
            if implied_prob > 0.90 and hours_left < 12:
                # Resolution risk – slight downward revision
                return implied_prob - 0.06

        return None   # No view – skip market

    # ── Public: set user-defined fair values ─────────────────────────────────
    def set_fair_value(self, token_id: str, fair_prob: float) -> None:
        """Called from the GUI Strategies tab."""
        self._fair_values[token_id] = max(0.01, min(0.99, fair_prob))
        log.info("[VB] Fair value set: token …%s → %.4f", token_id[-8:], fair_prob)

    def clear_fair_value(self, token_id: str) -> None:
        self._fair_values.pop(token_id, None)
