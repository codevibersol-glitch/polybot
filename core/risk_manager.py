"""
core/risk_manager.py
────────────────────
Hard-stop risk rules applied BEFORE every order is placed.

Rules (all configurable at runtime):
  1. Per-market position cap      – max USDC cost basis in any single market
  2. Total portfolio exposure cap – sum of all open position cost bases
  3. Daily loss stop              – if realized P/L today < −limit, halt trading
  4. Max order size               – single-order guard

Also provides Kelly-fraction position sizing.

Usage
─────
    rm = RiskManager.instance()
    rm.update_config(cfg["risk"])

    # In order placement:
    if not rm.check_order(token_id, market_id, side, size):
        return None  # blocked

    # After order placed:
    rm.register_order(token_id, market_id, side, size)

    # Kelly sizing:
    size = rm.kelly_size(edge=0.06, price=0.40, kelly_fraction=0.25,
                          bankroll=500, max_usd=50)
"""

import threading
import time
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

_MIDNIGHT_SECS = 86400   # seconds in a day


class RiskManager:
    """Singleton risk gate."""

    _instance: "RiskManager | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        # Config limits (updated from config.json)
        self._max_per_market     = 50.0
        self._max_total_exposure = 200.0
        self._max_daily_loss     = 50.0
        self._max_order_size     = 100.0   # single order guard

        # Runtime state
        self._market_exposure: dict[str, float] = {}   # market_id → USDC
        self._total_exposure:  float = 0.0
        self._daily_loss:      float = 0.0
        self._day_start:       float = time.time()
        self._halted:          bool  = False

        self._data_lock = threading.RLock()

    @classmethod
    def instance(cls) -> "RiskManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ── Config update ─────────────────────────────────────────────────────────
    def update_config(self, risk_cfg: dict) -> None:
        with self._data_lock:
            self._max_per_market     = float(risk_cfg.get("max_per_market_usd",     self._max_per_market))
            self._max_total_exposure = float(risk_cfg.get("max_total_exposure_usd", self._max_total_exposure))
            self._max_daily_loss     = float(risk_cfg.get("max_daily_loss_usd",     self._max_daily_loss))
        log.info(
            "Risk config: per_market=%.0f total=%.0f daily_loss_stop=%.0f",
            self._max_per_market, self._max_total_exposure, self._max_daily_loss,
        )

    # ── Pre-order check ───────────────────────────────────────────────────────
    def check_order(
        self,
        token_id: str,
        market_id: str,
        side: str,
        size: float,   # USDC
    ) -> bool:
        """
        Return True if the order is allowed under current risk rules.
        Logs the specific rule that blocks the order.
        """
        with self._data_lock:
            # Reset daily P/L counter at midnight
            self._maybe_reset_daily()

            if self._halted:
                log.warning("RISK: Trading halted (daily loss stop hit).")
                return False

            if size > self._max_order_size:
                log.warning("RISK: Order size %.2f exceeds max order cap %.2f", size, self._max_order_size)
                return False

            if side == "BUY":
                new_market_exp = self._market_exposure.get(market_id, 0.0) + size
                if new_market_exp > self._max_per_market:
                    log.warning(
                        "RISK: Market cap breached – current=%.2f + new=%.2f > limit=%.2f",
                        self._market_exposure.get(market_id, 0.0), size, self._max_per_market,
                    )
                    return False

                new_total = self._total_exposure + size
                if new_total > self._max_total_exposure:
                    log.warning(
                        "RISK: Total exposure cap breached – current=%.2f + new=%.2f > limit=%.2f",
                        self._total_exposure, size, self._max_total_exposure,
                    )
                    return False

        return True

    def register_order(
        self, token_id: str, market_id: str, side: str, size: float
    ) -> None:
        """Update exposure after a successful order placement."""
        with self._data_lock:
            if side == "BUY":
                self._market_exposure[market_id] = (
                    self._market_exposure.get(market_id, 0.0) + size
                )
                self._total_exposure += size

    def register_fill(self, market_id: str, side: str, size: float, pnl: float = 0.0) -> None:
        """Update exposure on fill + track realized P/L for daily stop."""
        with self._data_lock:
            if side == "SELL":
                # Reduce exposure
                self._market_exposure[market_id] = max(
                    0.0, self._market_exposure.get(market_id, 0.0) - size
                )
                self._total_exposure = max(0.0, self._total_exposure - size)
                # Accumulate daily P/L
                self._daily_loss -= pnl   # daily_loss is positive when losing
                if self._daily_loss >= self._max_daily_loss:
                    self._halted = True
                    log.error(
                        "RISK: Daily loss stop triggered – %.2f loss today!  Trading halted.",
                        self._daily_loss,
                    )

    def resume(self) -> None:
        """Manually resume trading after a halt (e.g. via GUI)."""
        with self._data_lock:
            self._halted = False
        log.warning("Risk halt CLEARED – trading resumed.")

    # ── Kelly position sizing ─────────────────────────────────────────────────
    @staticmethod
    def kelly_size(
        edge: float,           # estimated_prob − implied_prob  (e.g. 0.06)
        price: float,          # implied probability / price    (e.g. 0.40)
        kelly_fraction: float, # fractional Kelly (e.g. 0.25 = "quarter Kelly")
        bankroll: float,       # available USDC
        max_usd: float,        # hard cap per trade
        min_usd: float = 1.0,  # min trade size (ignore tiny edges)
    ) -> float:
        """
        Kelly-inspired position sizing.

        Full Kelly fraction = edge / (1 − price)
        We use kelly_fraction × full_kelly to be conservative.

        Returns USDC size to trade (0 if edge is negative or too small).
        """
        if edge <= 0 or price <= 0 or price >= 1:
            return 0.0
        # Odds in decimal form (how much you win per dollar bet)
        odds = (1.0 - price) / price
        # Full Kelly fraction of bankroll
        full_kelly = (edge * odds - (1 - edge)) / odds  # standard formula
        if full_kelly <= 0:
            return 0.0
        size = bankroll * full_kelly * kelly_fraction
        size = max(min_usd, min(size, max_usd))
        return round(size, 2)

    # ── Status ────────────────────────────────────────────────────────────────
    def status(self) -> dict:
        with self._data_lock:
            return {
                "halted":           self._halted,
                "total_exposure":   round(self._total_exposure, 2),
                "max_exposure":     self._max_total_exposure,
                "daily_loss":       round(self._daily_loss, 2),
                "max_daily_loss":   self._max_daily_loss,
                "market_exposure":  dict(self._market_exposure),
            }

    # ── Internal ──────────────────────────────────────────────────────────────
    def _maybe_reset_daily(self) -> None:
        """Reset daily P/L counter at midnight."""
        now = time.time()
        if now - self._day_start >= _MIDNIGHT_SECS:
            self._daily_loss = 0.0
            self._day_start  = now
            if self._halted:
                # Auto-resume at the start of a new day
                self._halted = False
                log.info("New trading day – daily loss counter reset, halt cleared.")
