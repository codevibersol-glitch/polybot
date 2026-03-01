"""
core/engine.py
──────────────
BotEngine – the central coordinator for the trading bot.

Responsibilities
─────────────────
  • Connect and authenticate with the Polymarket CLOB.
  • Start / stop WebSocket feeds.
  • Load historical trades → bootstrap position manager.
  • Run the strategy loop on a background thread.
  • Periodically refresh market data and prices.
  • Expose watched_markets / auto_trade_markets sets for strategies to filter.
  • Forward fill events from WS → order manager → position manager → strategies.

Threading model
───────────────
  Main thread  : GUI event loop (customtkinter)
  Engine thread: runs _engine_loop() – strategy ticks + price refreshes
  WS threads   : MarketWsWorker and UserWsWorker (daemon threads in websocket_manager)

Communication to GUI
─────────────────────
  BotEngine pushes status dicts to `gui_queue` (set by App) so the GUI
  can update labels / tables without touching engine internals.
"""

import queue
import threading
import time
from typing import Optional, Set

from utils.logger import get_logger
from utils.config import get_nested
from core.client import PolyClient
from core.market_data import MarketDataService
from core.websocket_manager import WebSocketManager
from core.order_manager import OrderManager
from core.position_manager import PositionManager
from core.risk_manager import RiskManager

log = get_logger(__name__)

_TICK_INTERVAL      = 15     # seconds between strategy ticks
_PRICE_REFRESH      = 10     # seconds between price map refresh
_MARKET_REFRESH     = 120    # seconds between full market list refresh
_ORDER_SYNC         = 30     # seconds between open-order reconciliation
_POSITION_REFRESH   = 5      # seconds between P/L recalculation
_BALANCE_REFRESH    = 60     # seconds between USDC cash balance fetch


class BotEngine:
    """Singleton trading engine."""

    _instance: "BotEngine | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._running         = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event      = threading.Event()

        # Market universe
        self.watched_markets:    Set[str] = set()   # market_ids shown in portfolio
        self.auto_trade_markets: Set[str] = set()   # market_ids actively traded

        # Live price map  {token_id → mid_price}
        self._price_map: dict[str, float] = {}
        self._price_lock = threading.Lock()

        # Cached market snapshot (list of market dicts with prices embedded)
        self._market_snapshot: list[dict] = []
        self._snapshot_lock   = threading.RLock()

        # Strategy instances (populated in connect())
        self._strategies: list = []

        # GUI update queue (set by App after engine creation)
        self.gui_queue: Optional[queue.Queue] = None

        # Timestamps for periodic tasks
        self._last_price_refresh  = 0.0
        self._last_market_refresh = 0.0
        self._last_order_sync     = 0.0
        self._last_position_ref   = 0.0
        self._last_balance_refresh = 0.0

        # USDC cash balance (fetched from CLOB, updated every _BALANCE_REFRESH s)
        self._cash_balance: float = 0.0
        self._wallet: str = ""

        # Tokens known to return 404 (resolved/closed markets) – never fetch again
        self._dead_tokens: Set[str] = set()

    @classmethod
    def instance(cls) -> "BotEngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ── Connect ───────────────────────────────────────────────────────────────
    def connect(self, private_key: str, wallet: str, sig_type: int, cfg: dict) -> None:
        """
        Full startup sequence:
          1. Connect CLOB client
          2. Apply risk config
          3. Bootstrap positions from trade history
          4. Start WS feeds
          5. Load strategies
          6. Start engine thread
        """
        log.info("=== PolyBot Engine Starting ===")
        self._wallet = wallet

        # 1. CLOB connection
        pc = PolyClient.instance()
        pc.connect(private_key, wallet, sig_type)

        # 1b. Fetch initial USDC cash balance
        try:
            self._cash_balance = pc.get_usdc_balance()
            log.info("USDC cash balance: $%.2f", self._cash_balance)
        except Exception as exc:
            log.warning("Could not fetch USDC balance: %s", exc)

        # 2. Risk config
        RiskManager.instance().update_config(cfg.get("risk", {}))

        # 3. Bootstrap positions — prefer Data API (accurate, no resolved-market junk)
        try:
            from core.market_data import MarketDataService
            api_positions = MarketDataService.fetch_user_positions(wallet)
            if api_positions:
                PositionManager.instance().load_from_positions_api(api_positions)
                # Also load realized P/L from closed positions
                closed = MarketDataService.fetch_user_closed_positions(wallet)
                if closed:
                    PositionManager.instance().load_from_closed_positions_api(closed)
            else:
                # Fall back to CLOB trade history if Data API returns nothing
                log.info("Data API returned no positions – falling back to trade history.")
                trades = pc.get_trades(limit=500)
                PositionManager.instance().load_from_trades(trades)
        except Exception as exc:
            log.warning("Could not bootstrap positions: %s", exc)

        # 4. WS feeds
        wm = WebSocketManager.instance()
        wm.on_book_update(self._on_book_update)
        wm.on_fill(self._on_fill_event)
        wm.start(
            api_key=pc.api_key,
            api_secret=pc.api_secret,
            passphrase=pc.api_passphrase,
        )

        # 5. Register watched / auto markets from config
        for mid in cfg.get("watched_markets", []):
            self.watched_markets.add(mid)
        for mid in cfg.get("auto_trade_markets", []):
            self.auto_trade_markets.add(mid)
            # Subscribe to WS for all tokens of this market
            self._subscribe_market_tokens(mid)

        # 6. Register fill→position→strategy pipeline
        def _fill_pipeline(order: dict, fill: dict) -> None:
            PositionManager.instance().on_fill(order, fill)
            for strat in self._strategies:
                if order.get("strategy") == strat.name:
                    strat.on_fill(order, fill)

        OrderManager.instance().on_fill(_fill_pipeline)

        # 7. Load strategies
        self._load_strategies(cfg)

        # 8. Initial market fetch – stamp the timer so the engine loop
        #    skips its own first refresh (avoids 3× fetch at startup)
        self._refresh_markets()
        self._last_market_refresh = time.time()

        # 8b. Pre-blacklist tokens from resolved/closed markets.
        #     Cross-reference position tokens against the active Gamma market
        #     list.  Any token not in the active set will 404 on the CLOB, so
        #     add it to _dead_tokens now – before the engine loop makes a
        #     single HTTP request.
        with self._snapshot_lock:
            active_token_ids = {
                tok["token_id"]
                for m in self._market_snapshot
                for tok in m.get("tokens", [])
                if tok.get("token_id")
            }
        for pos in PositionManager.instance().positions():
            tid = pos["token_id"]
            if tid not in active_token_ids:
                self._dead_tokens.add(tid)
        if self._dead_tokens:
            log.info(
                "Pre-blacklisted %d resolved-market tokens – 0 HTTP requests needed.",
                len(self._dead_tokens),
            )

        # 9. Start engine loop
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._engine_loop, name="EngineLoop", daemon=True
        )
        self._thread.start()
        log.info("=== Engine running ===")

    # ── Stop ──────────────────────────────────────────────────────────────────
    def stop(self) -> None:
        log.info("Engine stopping…")
        self._stop_event.set()
        self._running = False
        # Cancel all open orders before exit
        try:
            OrderManager.instance().cancel_all()
        except Exception as exc:
            log.warning("Cancel-all on shutdown failed: %s", exc)
        # Stop WS
        WebSocketManager.instance().stop()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Engine stopped.")

    @property
    def running(self) -> bool:
        return self._running

    # ── Engine loop (background thread) ───────────────────────────────────────
    def _engine_loop(self) -> None:
        """Main periodic loop – runs forever until stop() is called."""
        while not self._stop_event.is_set():
            now = time.time()

            # Refresh full market list
            if now - self._last_market_refresh >= _MARKET_REFRESH:
                self._refresh_markets()
                self._last_market_refresh = now

            # Refresh prices from CLOB order books
            if now - self._last_price_refresh >= _PRICE_REFRESH:
                self._refresh_prices()
                self._last_price_refresh = now

            # Refresh USDC cash balance
            if now - self._last_balance_refresh >= _BALANCE_REFRESH:
                self._refresh_cash_balance()
                self._last_balance_refresh = now

            # Update position P/L with latest prices
            if now - self._last_position_ref >= _POSITION_REFRESH:
                with self._price_lock:
                    pm_copy = dict(self._price_map)
                PositionManager.instance().update_prices(pm_copy)
                self._last_position_ref = now
                self._push_gui_update()

            # Reconcile open orders with CLOB
            if now - self._last_order_sync >= _ORDER_SYNC:
                OrderManager.instance().sync_open_orders()
                self._last_order_sync = now

            # Strategy ticks
            with self._snapshot_lock:
                snapshot = list(self._market_snapshot)
            with self._price_lock:
                price_copy = dict(self._price_map)

            for strat in self._strategies:
                strat.tick(snapshot, price_copy)

            # Sleep until next tick (but wake early if stop requested)
            self._stop_event.wait(timeout=_TICK_INTERVAL)

    # ── Market / price refresh ─────────────────────────────────────────────────
    def _refresh_markets(self) -> None:
        """Fetch all active markets from Gamma API and rebuild snapshot."""
        try:
            markets = MarketDataService.instance().fetch_markets(force=True)
            # Embed live Gamma prices into snapshot for strategies that
            # don't fetch their own order books
            with self._snapshot_lock:
                self._market_snapshot = markets
            log.debug("Market snapshot updated: %d markets.", len(markets))
        except Exception as exc:
            log.error("Market refresh failed: %s", exc)

    def _refresh_cash_balance(self) -> None:
        """Fetch current USDC cash balance from the CLOB."""
        try:
            bal = PolyClient.instance().get_usdc_balance()
            self._cash_balance = bal
            log.debug("USDC cash balance refreshed: $%.2f", bal)
        except Exception as exc:
            log.debug("Cash balance refresh failed: %s", exc)

    def _refresh_prices(self) -> None:
        """
        Fetch CLOB order-book mid-prices for:
          • All tokens in watched/auto markets (every cycle)
          • All tokens in the market snapshot that we've never priced before
            (first-time only – populates the Markets tab live prices)
          • All position tokens
        Subscribes each fetched token to the WS feed for ongoing updates.
        """
        tokens_to_fetch = set()
        with self._snapshot_lock:
            snap = list(self._market_snapshot)

        relevant_ids = self.watched_markets | self.auto_trade_markets
        with self._price_lock:
            have_prices = set(self._price_map.keys())

        for m in snap:
            mid = m.get("id") or m.get("conditionId", "")
            for tok in m.get("tokens", []):
                tid = tok.get("token_id")
                if not tid:
                    continue
                # Always fetch for watched/auto markets; first-time for all others
                if mid in relevant_ids or tid not in have_prices:
                    tokens_to_fetch.add(tid)

        # Also add tokens from open positions
        for pos in PositionManager.instance().positions():
            tokens_to_fetch.add(pos["token_id"])

        new_prices: dict[str, float] = {}
        pc = PolyClient.instance()

        for token_id in tokens_to_fetch:
            if token_id in self._dead_tokens:
                continue
            try:
                book = pc.get_order_book(token_id)
                mid  = MarketDataService.midpoint_from_book(book)
                if mid is not None:
                    new_prices[token_id] = mid
                    WebSocketManager.instance().subscribe_market_token(token_id)
            except Exception as exc:
                if "404" in str(exc) or "no orderbook" in str(exc).lower():
                    self._dead_tokens.add(token_id)
                    log.debug("Token %s…%s is dead (404) – blacklisted.", token_id[:8], token_id[-4:])

        with self._price_lock:
            self._price_map.update(new_prices)

    # ── WS callbacks ─────────────────────────────────────────────────────────
    def _on_book_update(self, msg: dict) -> None:
        """Receive real-time book update from market WS."""
        token_id = msg.get("asset_id") or msg.get("assetId", "")
        price_str = msg.get("price") or msg.get("midpoint") or msg.get("last_trade_price")
        if token_id and price_str:
            try:
                with self._price_lock:
                    self._price_map[token_id] = float(price_str)
            except ValueError:
                pass

    def _on_fill_event(self, fill: dict) -> None:
        """Receive fill event from user WS – route to order manager."""
        OrderManager.instance().handle_fill_event(fill)

    # ── Market subscription management ────────────────────────────────────────
    def add_watched_market(self, market_id: str) -> None:
        self.watched_markets.add(market_id)
        self._subscribe_market_tokens(market_id)

    def remove_watched_market(self, market_id: str) -> None:
        self.watched_markets.discard(market_id)

    def add_auto_trade_market(self, market_id: str) -> None:
        self.auto_trade_markets.add(market_id)
        self._subscribe_market_tokens(market_id)

    def remove_auto_trade_market(self, market_id: str) -> None:
        self.auto_trade_markets.discard(market_id)
        # Cancel all open orders for this market
        OrderManager.instance().cancel_strategy_orders("market_making")

    def _subscribe_market_tokens(self, market_id: str) -> None:
        """Subscribe to WS price feed for all tokens of a market."""
        mds = MarketDataService.instance()
        market = mds.get_market_by_id(market_id)
        if not market:
            return
        for tok in market.get("tokens", []):
            tid = tok.get("token_id")
            if tid:
                WebSocketManager.instance().subscribe_market_token(tid)

    # ── Strategy management ────────────────────────────────────────────────────
    def _load_strategies(self, cfg: dict) -> None:
        """Instantiate all strategy objects with their config."""
        from strategies.market_making import MarketMakingStrategy
        from strategies.value_betting import ValueBettingStrategy
        from strategies.copy_trading import CopyTradingStrategy
        from strategies.time_decay import TimeDecayStrategy

        strat_cfg = cfg.get("strategies", {})
        self._strategies = [
            MarketMakingStrategy(strat_cfg.get("market_making", {})),
            ValueBettingStrategy(strat_cfg.get("value_betting", {})),
            CopyTradingStrategy(strat_cfg.get("copy_trading", {})),
            TimeDecayStrategy(strat_cfg.get("time_decay", {})),
        ]
        log.info("Loaded %d strategies.", len(self._strategies))

    def get_strategy(self, name: str):
        """Return a strategy instance by name."""
        for s in self._strategies:
            if s.name == name:
                return s
        return None

    def reload_strategy_config(self, name: str, new_config: dict) -> None:
        """Hot-reload config for a strategy (called from Strategies tab)."""
        strat = self.get_strategy(name)
        if strat:
            strat.update_config(new_config)

    # ── GUI update ────────────────────────────────────────────────────────────
    def _push_gui_update(self) -> None:
        """Push a status snapshot to the GUI queue."""
        if not self.gui_queue:
            return
        try:
            pm = PositionManager.instance()
            rm = RiskManager.instance()
            risk_st = rm.status()

            # Only show / count positions for live (non-404) markets.
            # Resolved markets never generate SELL trades on settlement, so the
            # position manager keeps all historical BUY positions "open".
            # Summing their stale market_value produces a wildly inflated
            # portfolio figure – only live tokens have a meaningful price.
            live_positions = [
                p for p in pm.positions()
                if p["token_id"] not in self._dead_tokens
            ]
            live_pv   = round(sum(p["market_value"]   for p in live_positions), 2)
            live_upnl = round(sum(p["unrealized_pnl"] for p in live_positions), 2)
            rpnl      = round(pm.total_realized_pnl(), 2)
            cash      = round(self._cash_balance, 2)
            # Total portfolio = open position market value + uninvested cash
            total_pv  = round(live_pv + cash, 2)

            self.gui_queue.put_nowait({
                "type": "status",
                "portfolio_value":    total_pv,
                "positions_value":    live_pv,
                "cash_balance":       cash,
                "unrealized_pnl":     live_upnl,
                "realized_pnl":       rpnl,
                "total_pnl":          round(live_upnl + rpnl, 2),
                "positions":          live_positions,
                "open_orders":        OrderManager.instance().open_orders(),
                "risk":               risk_st,
                "strategies_enabled": {s.name: s.enabled for s in self._strategies},
            })
        except queue.Full:
            pass   # GUI is slow – skip this update

    # ── Manual order from GUI ─────────────────────────────────────────────────
    def manual_order(
        self,
        token_id: str,
        market_id: str,
        question: str,
        side: str,
        price: float,
        size: float,
    ) -> Optional[str]:
        """Place a manual order from the Logs tab."""
        return OrderManager.instance().place_order(
            token_id=token_id,
            market_id=market_id,
            question=question,
            side=side,
            price=price,
            size=size,
            strategy="manual",
        )

    # ── Current snapshot for GUI ──────────────────────────────────────────────
    def get_markets_snapshot(self) -> list[dict]:
        with self._snapshot_lock:
            return list(self._market_snapshot)

    def get_price_map(self) -> dict:
        with self._price_lock:
            return dict(self._price_map)
