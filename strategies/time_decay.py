"""
strategies/time_decay.py
────────────────────────
Time-Decay / Pre-Expiry Liquidation Strategy

Logic summary
─────────────
  For each open position where the market is within `hours_before_expiry`
  of resolution:

    Scenario A – We hold YES tokens:
      • If price has NOT moved decisively toward 1.0 (i.e., event likely
        hasn't resolved), sell YES before final resolution risk.
      • Threshold: current_price < 0.90.

    Scenario B – We hold NO tokens (the core alpha trade):
      • As deadline approaches without a confirming news spike, the "No"
        token's true probability rises toward 1.0.
      • If current No price ≥ min_no_price (e.g. 0.85) AND time < threshold,
        sell NO for profit lock.

    Scenario C – Unresolved market approaching expiry with no position:
      • Attempt to buy NO if No-price < 0.80 and < hours_before_expiry.
        (Classic time-decay bet: buy cheap No on stale/uncertain markets.)

Config keys:
  hours_before_expiry : int    hours before end date to trigger (e.g. 6)
  min_no_price        : float  minimum No price to trigger profit-lock (0.85)
"""

import time
from typing import Optional

import dateutil.parser as dp

from strategies.base import BaseStrategy
from core.order_manager import OrderManager
from core.position_manager import PositionManager
from core.risk_manager import RiskManager
from utils.logger import get_logger

log = get_logger(__name__)

_STRATEGY   = "time_decay"
_SELL_COOLDOWN = 300   # don't sell the same token more often than every 5 min


class TimeDecayStrategy(BaseStrategy):
    """
    Monetises time decay of prediction-market prices as resolution approaches.

    Extend by overriding _should_sell_yes() / _should_sell_no()
    with your own resolution-signal model.
    """

    name = "time_decay"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._last_action: dict[str, float] = {}   # token_id → timestamp

    # ── Tick ──────────────────────────────────────────────────────────────────
    def on_tick(self, market_snapshot: list, price_map: dict) -> None:
        hours_threshold = float(self.cfg("hours_before_expiry", 6))
        min_no_price    = float(self.cfg("min_no_price", 0.85))
        max_pos         = float(self.cfg("max_pos_usd", 30.0))

        # ── Manage existing positions ─────────────────────────────────────────
        for pos in PositionManager.instance().positions():
            if pos["shares"] < 0.1:
                continue
            token_id  = pos["token_id"]
            hours_left = self._hours_to_expiry(pos.get("expiry", ""))
            if hours_left is None or hours_left > hours_threshold:
                continue

            current = price_map.get(token_id, pos["current_price"])

            # Determine if this is a Yes or No position
            # Use outcome field set by position manager
            outcome = pos.get("outcome", "Yes")

            if outcome == "Yes" and self._should_sell_yes(current, hours_left):
                self._sell_position(pos, current, reason="pre-expiry YES liquidation")

            elif outcome == "No" and current >= min_no_price:
                if self._should_sell_no(current, hours_left):
                    self._sell_position(pos, current, reason="No profit lock near expiry")

        # ── Scan for new time-decay buys ──────────────────────────────────────
        for market in market_snapshot:
            end_str = market.get("endDateIso") or market.get("end_date_iso", "")
            hours_left = self._hours_to_expiry(end_str)
            if hours_left is None or hours_left > hours_threshold:
                continue

            no_token = self.token_for_outcome(market, "No")
            if not no_token:
                continue

            no_price = 1.0 - float(
                next(
                    (t["price"] for t in market.get("tokens", []) if t.get("outcome") == "Yes"),
                    0.5,
                )
            )

            # Only buy No if it's cheap (event hasn't resolved) and we have no position
            existing_shares = PositionManager.instance().shares_held(no_token)
            if existing_shares > 0.1:
                continue

            if no_price < 0.82 and hours_left < hours_threshold:
                # Check cooldown
                if time.time() - self._last_action.get(no_token + "BUY", 0) < _SELL_COOLDOWN * 2:
                    continue

                market_id = market.get("id") or market.get("conditionId", "")
                size = float(self.cfg("max_pos_usd", 30.0)) * 0.3   # conservative

                if RiskManager.instance().check_order(no_token, market_id, "BUY", size):
                    log.info(
                        "[TD] Time-decay BUY No: %s @ %.4f  (%.1fh left)",
                        market.get("question", "?")[:40], no_price, hours_left,
                    )
                    OrderManager.instance().place_order(
                        token_id=no_token,
                        market_id=market_id,
                        question=market.get("question", ""),
                        side="BUY",
                        price=round(no_price + 0.01, 4),
                        size=round(size, 2),
                        strategy=_STRATEGY,
                    )
                    self._last_action[no_token + "BUY"] = time.time()

    # ── Sell triggers ─────────────────────────────────────────────────────────
    def _should_sell_yes(self, current_price: float, hours_left: float) -> bool:
        """
        Sell Yes tokens before expiry if price hasn't reached near-certainty.
        Override with a news/resolution signal for better timing.
        """
        if current_price > 0.90:
            return False   # likely to resolve YES – hold for max value
        if hours_left < 2 and current_price < 0.70:
            return True    # imminent expiry with low probability – cut loss
        if hours_left < 6 and current_price < 0.80:
            return True    # pre-expiry – reduce uncertainty
        return False

    def _should_sell_no(self, current_price: float, hours_left: float) -> bool:
        """
        Lock in No profits as deadline approaches without resolution.
        """
        if hours_left < 1:
            return True    # very near expiry – take profit
        if hours_left < 3 and current_price > 0.88:
            return True    # high confidence No + near expiry
        return False

    # ── Sell execution ────────────────────────────────────────────────────────
    def _sell_position(self, pos: dict, current_price: float, reason: str) -> None:
        token_id = pos["token_id"]
        shares   = pos["shares"]

        if time.time() - self._last_action.get(token_id + "SELL", 0) < _SELL_COOLDOWN:
            return   # cooldown guard

        # USDC value = shares × price
        size_usd = round(shares * current_price, 2)
        if size_usd < 0.50:
            return   # not worth the gas / fee

        log.info(
            "[TD] %s – selling %.3f shares @ %.4f (%.2f USDC) – %s",
            pos["question"][:40], shares, current_price, size_usd, reason,
        )

        OrderManager.instance().place_order(
            token_id=token_id,
            market_id=pos["market_id"],
            question=pos["question"],
            side="SELL",
            price=round(current_price - 0.01, 4),  # slightly aggressive to get filled
            size=size_usd,
            strategy=_STRATEGY,
        )
        self._last_action[token_id + "SELL"] = time.time()

    # ── Helper ────────────────────────────────────────────────────────────────
    @staticmethod
    def _hours_to_expiry(end_str: str) -> Optional[float]:
        if not end_str:
            return None
        try:
            end_dt = dp.parse(end_str)
            import datetime
            if end_dt.tzinfo:
                from datetime import timezone
                now_dt = datetime.datetime.now(timezone.utc)
            else:
                now_dt = datetime.datetime.utcnow()
            hours = (end_dt - now_dt).total_seconds() / 3600
            return max(0.0, hours)
        except Exception:
            return None
