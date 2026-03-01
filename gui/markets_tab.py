"""
gui/markets_tab.py
──────────────────
Markets Tab – browse, search, and manage Polymarket prediction markets.

Layout
──────
  [ Search: ____________ ] [ Refresh ]  [ Sort: Volume ▼ ]   20 markets
  ┌──────────────────────────────────────────────────────────┐
  │ Question │ Yes% │ No% │ Liquidity │ Vol 24h │ Expiry │ ⚙ │
  │ …        │ 62%  │ 38% │ $45,000   │ $5,000  │ Nov 5  │ W │
  └──────────────────────────────────────────────────────────┘
  Buttons per row:  [Watch]  [Auto-Trade]  [Stop]

Prices in Yes/No columns update every 3 seconds from the engine's live
price_map without recreating any widgets.  Full row rebuild only happens
when the market list changes (Refresh or filter).
"""

import threading
from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from gui.app import PolybotApp

from utils.logger import get_logger

log = get_logger(__name__)

GREEN  = "#4caf50"
RED    = "#f44336"
YELLOW = "#ffc107"
TEXT   = "#e0e0e0"
SUB    = "#9e9e9e"
BG     = "#1a1a2e"
SURF   = "#16213e"
ACCENT = "#0f3460"
HILIT  = "#533483"

_COLS = [
    ("Question",    400),
    ("Yes %",        65),
    ("No %",         65),
    ("Liquidity",    95),
    ("Vol 24h",      95),
    ("Expiry",       90),
    ("Status",       80),
]

# Column indices for fast access
_IDX_YES = 1
_IDX_NO  = 2


class MarketsTab:
    """Market browser with search, sort, and one-click trading actions."""

    def __init__(self, parent: ctk.CTkFrame, app: "PolybotApp") -> None:
        self.app = app
        self._all_markets: list[dict] = []
        self._displayed:   list[dict] = []
        self._rows:        list[dict] = []   # {frame, labels, market}
        self._sort_col = "volume24hr"
        self._sort_rev = True
        self._build(parent)
        # Start periodic price refresh (updates Yes/No % without rebuilding rows)
        self.app.after(3000, self._schedule_price_updates)

    def _build(self, parent: ctk.CTkFrame) -> None:
        parent.configure(fg_color=BG)

        # ── Toolbar ────────────────────────────────────────────────────────
        toolbar = ctk.CTkFrame(parent, fg_color=BG)
        toolbar.pack(fill="x", padx=12, pady=8)

        ctk.CTkLabel(toolbar, text="Search:", text_color=TEXT).pack(side="left")
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(
            toolbar, textvariable=self._search_var, width=300,
            placeholder_text="Search markets…",
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            toolbar, text="Refresh", width=90,
            command=self._async_refresh,
            fg_color=ACCENT, hover_color="#1a4a80",
        ).pack(side="left", padx=4)

        ctk.CTkLabel(toolbar, text="Sort:", text_color=SUB).pack(side="left", padx=(16, 4))
        self._sort_var = ctk.StringVar(value="Volume 24h")
        ctk.CTkOptionMenu(
            toolbar,
            variable=self._sort_var,
            values=["Volume 24h", "Liquidity", "Expiry", "Yes Price"],
            command=self._on_sort_change,
            fg_color=ACCENT, button_color=HILIT, width=140,
        ).pack(side="left")

        self._count_label = ctk.CTkLabel(
            toolbar, text="0 markets", text_color=SUB, font=ctk.CTkFont(size=11),
        )
        self._count_label.pack(side="right", padx=8)

        # ── Table ──────────────────────────────────────────────────────────
        self._table_frame = ctk.CTkScrollableFrame(parent, fg_color=SURF)
        self._table_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        # Header row
        hdr = ctk.CTkFrame(self._table_frame, fg_color=ACCENT)
        hdr.pack(fill="x")
        for col, width in _COLS:
            ctk.CTkLabel(
                hdr, text=col, width=width, anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT,
            ).pack(side="left", padx=4, pady=3)
        ctk.CTkLabel(
            hdr, text="Actions", width=160, anchor="w",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT,
        ).pack(side="left", padx=4)

    # ── Data loading ──────────────────────────────────────────────────────────
    def _async_refresh(self) -> None:
        """Fetch markets on a background thread so GUI stays responsive."""
        self._count_label.configure(text="Loading…")

        def _fetch():
            from core.market_data import MarketDataService
            try:
                markets = MarketDataService.instance().fetch_markets(force=True)
                self.app.gui_queue.put_nowait({"type": "markets_loaded", "markets": markets})
            except Exception as exc:
                log.error("Market fetch failed: %s", exc)
                self.app.gui_queue.put_nowait({"type": "markets_loaded", "markets": []})

        threading.Thread(target=_fetch, daemon=True).start()

    def refresh_table(self, markets: list[dict]) -> None:
        """Called on main thread via gui_queue after markets are fetched."""
        self._all_markets = markets
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._search_var.get().strip().lower()
        filtered = [
            m for m in self._all_markets
            if not query
            or query in m.get("question", "").lower()
            or query in m.get("slug", "").lower()
        ]

        key_map = {
            "Volume 24h": lambda m: float(m.get("volume24hr", m.get("volume", 0)) or 0),
            "Liquidity":  lambda m: float(m.get("liquidity", 0) or 0),
            "Expiry":     lambda m: m.get("endDateIso", "") or "",
            "Yes Price":  lambda m: self._yes_price(m),
        }
        sort_fn = key_map.get(self._sort_var.get(), key_map["Volume 24h"])
        filtered.sort(key=sort_fn, reverse=self._sort_rev)

        self._displayed = filtered
        self._count_label.configure(text=f"{len(filtered)} markets")
        self._render_rows()

    def _on_sort_change(self, value: str) -> None:
        self._sort_rev = True
        self._apply_filter()

    # ── Row rendering ─────────────────────────────────────────────────────────
    def _render_rows(self) -> None:
        """Rebuild the visible rows.  Called only when the market list changes."""
        for row in self._rows:
            row["frame"].destroy()
        self._rows = []

        from core.engine import BotEngine
        engine = BotEngine.instance()
        price_map = engine.get_price_map() if engine.running else {}

        for idx, market in enumerate(self._displayed):
            bg = SURF if idx % 2 == 0 else "#1c2a48"
            row_frame = ctk.CTkFrame(self._table_frame, fg_color=bg, corner_radius=0)
            row_frame.pack(fill="x")

            yes_p  = self._yes_price(market, price_map)
            labels = []
            for col, width in _COLS:
                val, color = self._cell(market, col, yes_p)
                lbl = ctk.CTkLabel(
                    row_frame, text=val, width=width, anchor="w",
                    font=ctk.CTkFont(size=11), text_color=color,
                )
                lbl.pack(side="left", padx=4, pady=2)
                labels.append(lbl)

            # Action buttons
            market_id  = market.get("id") or market.get("conditionId", "")
            is_auto    = market_id in engine.auto_trade_markets
            is_watched = market_id in engine.watched_markets

            btn_frame = ctk.CTkFrame(row_frame, fg_color=bg, width=160)
            btn_frame.pack(side="left", padx=4)

            if not is_watched and not is_auto:
                ctk.CTkButton(
                    btn_frame, text="Watch", width=60, height=22,
                    font=ctk.CTkFont(size=10),
                    fg_color=ACCENT, hover_color="#1a4a80",
                    command=lambda m=market: self._watch(m),
                ).pack(side="left", padx=2)

            if not is_auto:
                ctk.CTkButton(
                    btn_frame, text="Auto-Trade", width=85, height=22,
                    font=ctk.CTkFont(size=10),
                    fg_color=HILIT, hover_color="#6a3d9a",
                    command=lambda m=market: self._auto_trade(m),
                ).pack(side="left", padx=2)
            else:
                ctk.CTkButton(
                    btn_frame, text="Stop", width=50, height=22,
                    font=ctk.CTkFont(size=10),
                    fg_color="#7b1111", hover_color="#a01c1c",
                    command=lambda mid=market_id: self._stop_auto(mid),
                ).pack(side="left", padx=2)

            self._rows.append({"frame": row_frame, "labels": labels, "market": market})

    # ── Live price updates (no widget recreate) ───────────────────────────────
    def _schedule_price_updates(self) -> None:
        """Called by tkinter's after() every 3 s to refresh Yes/No prices."""
        self._update_prices()
        self.app.after(3000, self._schedule_price_updates)

    def _update_prices(self) -> None:
        """Update only the Yes % / No % label text in existing rows."""
        if not self._rows:
            return
        from core.engine import BotEngine
        engine = BotEngine.instance()
        if not engine.running:
            return
        price_map = engine.get_price_map()
        for row_info in self._rows:
            market = row_info["market"]
            labels = row_info["labels"]
            p = self._yes_price(market, price_map)
            if p > 0:
                yes_c = GREEN if p > 0.65 else (RED if p < 0.35 else YELLOW)
                no_c  = GREEN if (1-p) > 0.65 else (RED if (1-p) < 0.35 else YELLOW)
                labels[_IDX_YES].configure(text=f"{p*100:.1f}%",     text_color=yes_c)
                labels[_IDX_NO ].configure(text=f"{(1-p)*100:.1f}%", text_color=no_c)

    # ── Cell value + colour (computed together to avoid redundant price lookup) ─
    @staticmethod
    def _cell(market: dict, col: str, yes_p: float) -> tuple:
        if col == "Question":
            return market.get("question", "")[:52], TEXT

        if col == "Yes %":
            if yes_p > 0:
                c = GREEN if yes_p > 0.65 else (RED if yes_p < 0.35 else YELLOW)
                return f"{yes_p*100:.1f}%", c
            return "--", SUB

        if col == "No %":
            no_p = 1.0 - yes_p
            if yes_p > 0:
                c = GREEN if no_p > 0.65 else (RED if no_p < 0.35 else YELLOW)
                return f"{no_p*100:.1f}%", c
            return "--", SUB

        if col == "Liquidity":
            v = float(market.get("liquidity", 0) or 0)
            return f"${v:,.0f}", TEXT

        if col == "Vol 24h":
            v = float(market.get("volume24hr", market.get("volume", 0)) or 0)
            return f"${v:,.0f}", TEXT

        if col == "Expiry":
            return (market.get("endDateIso", "") or "")[:10], TEXT

        if col == "Status":
            if market.get("closed", False):
                return "Closed", RED
            return ("Active" if market.get("active", True) else "Inactive"), TEXT

        return "", TEXT

    @staticmethod
    def _yes_price(market: dict, price_map: dict = None) -> float:
        """
        Return the Yes-token price (0-1).  Priority:
          1. Engine live price_map (from CLOB order books / WS)
          2. Gamma API token price (case-insensitive outcome match)
          3. Gamma API outcomePrices list
        Returns 0.0 if unknown.
        """
        # 1. Live price map from engine
        if price_map:
            for tok in market.get("tokens", []):
                if str(tok.get("outcome", "")).lower() == "yes":
                    tid = tok.get("token_id")
                    if tid and tid in price_map:
                        return price_map[tid]

        # 2. Gamma API token price (case-insensitive)
        for tok in market.get("tokens", []):
            if str(tok.get("outcome", "")).lower() == "yes":
                try:
                    p = float(tok.get("price", 0) or 0)
                    if p > 0:
                        return p
                except (TypeError, ValueError):
                    pass

        # 3. outcomePrices fallback
        try:
            prices = market.get("outcomePrices", [])
            if prices:
                p = float(prices[0] or 0)
                if p > 0:
                    return p
        except Exception:
            pass

        return 0.0

    # ── Action handlers ───────────────────────────────────────────────────────
    def _watch(self, market: dict) -> None:
        mid = market.get("id") or market.get("conditionId", "")
        from core.engine import BotEngine
        BotEngine.instance().add_watched_market(mid)
        if mid not in self.app.config.get("watched_markets", []):
            self.app.config.setdefault("watched_markets", []).append(mid)
        self._apply_filter()
        log.info("Watching market: %s", market.get("question", "")[:40])

    def _auto_trade(self, market: dict) -> None:
        from tkinter import messagebox
        if not messagebox.askyesno(
            "Enable Auto-Trade",
            f"Start automated trading on:\n\n{market.get('question', '')}\n\n"
            "Make sure your strategies and risk limits are configured first."
        ):
            return
        mid = market.get("id") or market.get("conditionId", "")
        from core.engine import BotEngine
        BotEngine.instance().add_auto_trade_market(mid)
        if mid not in self.app.config.get("auto_trade_markets", []):
            self.app.config.setdefault("auto_trade_markets", []).append(mid)
        self._apply_filter()
        log.info("Auto-trading enabled: %s", market.get("question", "")[:40])

    def _stop_auto(self, market_id: str) -> None:
        from core.engine import BotEngine
        BotEngine.instance().remove_auto_trade_market(market_id)
        cfg = self.app.config.get("auto_trade_markets", [])
        if market_id in cfg:
            cfg.remove(market_id)
        self._apply_filter()
        log.info("Auto-trading stopped for market %s…", market_id[:12])
