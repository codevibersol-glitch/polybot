"""
gui/dashboard_tab.py
─────────────────────
Dashboard Tab – real-time portfolio overview.

Layout
──────
  ┌──── Summary Cards ────────────────────────────────────────┐
  │  Portfolio Value  │  Total P/L  │  Unrealized  │  Realized │
  └───────────────────────────────────────────────────────────┘
  ┌──── Open Positions Table ─────────────────────────────────┐
  │ Market │ Outcome │ Shares │ Entry │ Current │ P/L │ Expiry │
  └───────────────────────────────────────────────────────────┘
  ┌──── Open Orders Table ────────────────────────────────────┐
  │ Market │ Side │ Size │ Price │ Status │ Strategy │ Age    │
  └───────────────────────────────────────────────────────────┘
  [ Export CSV ]  [ Cancel All ]
"""

import time
from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from gui.app import PolybotApp

from utils.logger import get_logger

log = get_logger(__name__)

# Colour palette (matches app.py)
GREEN  = "#4caf50"
RED    = "#f44336"
YELLOW = "#ffc107"
TEXT   = "#e0e0e0"
SUB    = "#9e9e9e"
BG     = "#1a1a2e"
SURF   = "#16213e"
ACCENT = "#0f3460"

# Table column definitions: (header, width)
_POS_COLS = [
    ("Market",       320),
    ("Outcome",       70),
    ("Shares",        70),
    ("Entry $",       70),
    ("Current $",     80),
    ("Mkt Value",     80),
    ("P/L",           80),
    ("Expiry",       100),
]
_ORD_COLS = [
    ("Market",       260),
    ("Side",          60),
    ("Size $",        70),
    ("Price",         70),
    ("Filled",        70),
    ("Status",        80),
    ("Strategy",     110),
    ("Age",           60),
]


class DashboardTab:
    """Builds and manages the Dashboard tab content."""

    def __init__(self, parent: ctk.CTkFrame, app: "PolybotApp") -> None:
        self.app = app
        self._build(parent)

    def _build(self, parent: ctk.CTkFrame) -> None:
        parent.configure(fg_color=BG)

        # ── Summary cards row ──────────────────────────────────────────────
        cards_row = ctk.CTkFrame(parent, fg_color=BG)
        cards_row.pack(fill="x", padx=12, pady=(12, 6))

        self._card_portfolio, self._card_cash = self._make_card(
            cards_row, "Portfolio Value", "$0.00", sub="Cash: $0.00"
        )
        self._card_total_pnl,  _ = self._make_card(cards_row, "Total P/L",      "$0.00")
        self._card_unrealised, _ = self._make_card(cards_row, "Unrealized P/L", "$0.00")
        self._card_realised,   _ = self._make_card(cards_row, "Realized P/L",   "$0.00")

        # ── Open Positions label + table ───────────────────────────────────
        ctk.CTkLabel(
            parent, text="Open Positions",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT, anchor="w",
        ).pack(fill="x", padx=14, pady=(8, 2))

        self._pos_frame = ctk.CTkScrollableFrame(parent, fg_color=SURF, height=210)
        self._pos_frame.pack(fill="x", padx=12, pady=(0, 4))
        self._build_table_headers(self._pos_frame, _POS_COLS)
        self._pos_rows: list[list[ctk.CTkLabel]] = []

        # ── Open Orders label + table ──────────────────────────────────────
        ctk.CTkLabel(
            parent, text="Open Orders",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT, anchor="w",
        ).pack(fill="x", padx=14, pady=(6, 2))

        self._ord_frame = ctk.CTkScrollableFrame(parent, fg_color=SURF, height=150)
        self._ord_frame.pack(fill="x", padx=12, pady=(0, 4))
        self._build_table_headers(self._ord_frame, _ORD_COLS)
        self._ord_rows: list[list[ctk.CTkLabel]] = []

        # ── Bottom buttons ─────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(parent, fg_color=BG)
        btn_row.pack(fill="x", padx=12, pady=4)

        ctk.CTkButton(
            btn_row, text="Export P/L to CSV", width=160,
            command=self._export_csv,
            fg_color=ACCENT, hover_color="#1a4a80",
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_row, text="⚡ Cancel All Orders", width=180,
            command=self._cancel_all,
            fg_color="#7b1111", hover_color="#a01c1c",
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_row, text="Refresh", width=100,
            command=self._manual_refresh,
            fg_color=ACCENT, hover_color="#1a4a80",
        ).pack(side="right", padx=4)

    # ── Card widget ────────────────────────────────────────────────────────────
    def _make_card(self, parent, title: str, value: str, sub: str = "") -> tuple:
        """Return (value_label, sub_label).  sub_label is None if sub not given."""
        card = ctk.CTkFrame(parent, fg_color=SURF, corner_radius=8)
        card.pack(side="left", expand=True, fill="x", padx=6)

        ctk.CTkLabel(
            card, text=title,
            font=ctk.CTkFont(size=11), text_color=SUB,
        ).pack(pady=(8, 0))

        val_lbl = ctk.CTkLabel(
            card, text=value,
            font=ctk.CTkFont(size=22, weight="bold"), text_color=TEXT,
        )
        val_lbl.pack(pady=(2, 0))

        sub_lbl = None
        if sub:
            sub_lbl = ctk.CTkLabel(
                card, text=sub,
                font=ctk.CTkFont(size=10), text_color=SUB,
            )
            sub_lbl.pack(pady=(0, 8))
        else:
            val_lbl.pack_configure(pady=(2, 10))

        return val_lbl, sub_lbl

    # ── Table helpers ──────────────────────────────────────────────────────────
    def _build_table_headers(self, parent: ctk.CTkScrollableFrame, cols: list) -> None:
        hdr = ctk.CTkFrame(parent, fg_color=ACCENT)
        hdr.pack(fill="x")
        for col, width in cols:
            ctk.CTkLabel(
                hdr, text=col, width=width, anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=TEXT,
            ).pack(side="left", padx=4, pady=3)

    def _ensure_rows(
        self, parent, existing_rows: list, n: int, cols: list
    ) -> list:
        """Grow or shrink the visible row pool to exactly n rows."""
        while len(existing_rows) < n:
            row_frame = ctk.CTkFrame(
                parent,
                fg_color=SURF if len(existing_rows) % 2 == 0 else "#1c2a48",
                corner_radius=0,
            )
            row_frame.pack(fill="x")
            labels = []
            for _, width in cols:
                lbl = ctk.CTkLabel(
                    row_frame, text="", width=width, anchor="w",
                    font=ctk.CTkFont(size=11), text_color=TEXT,
                )
                lbl.pack(side="left", padx=4, pady=2)
                labels.append(lbl)
            existing_rows.append(labels)

        # Hide extra rows
        for i in range(n, len(existing_rows)):
            for lbl in existing_rows[i]:
                lbl.configure(text="")

        return existing_rows

    # ── Update from engine status dict ────────────────────────────────────────
    def update_status(self, msg: dict) -> None:
        """Called by App._apply_status() on every engine update."""
        pv   = msg.get("portfolio_value", 0)   # positions value + cash
        cash = msg.get("cash_balance", 0)
        upnl = msg.get("unrealized_pnl", 0)
        rpnl = msg.get("realized_pnl", 0)
        tpnl = msg.get("total_pnl", 0)

        self._card_portfolio.configure(text=f"${pv:,.2f}")
        if self._card_cash is not None:
            self._card_cash.configure(text=f"Cash: ${cash:,.2f}")

        pnl_color = GREEN if tpnl >= 0 else RED
        self._card_total_pnl.configure(
            text=f"{'+'if tpnl>=0 else ''}{tpnl:,.2f}",
            text_color=pnl_color,
        )
        self._card_unrealised.configure(
            text=f"{'+'if upnl>=0 else ''}{upnl:,.2f}",
            text_color=GREEN if upnl >= 0 else RED,
        )
        self._card_realised.configure(
            text=f"{'+'if rpnl>=0 else ''}{rpnl:,.2f}",
            text_color=GREEN if rpnl >= 0 else RED,
        )

        # Update positions table
        positions = msg.get("positions", [])
        self._ensure_rows(self._pos_frame, self._pos_rows, len(positions), _POS_COLS)

        for i, pos in enumerate(positions):
            pnl   = pos.get("unrealized_pnl", 0)
            color = GREEN if pnl >= 0 else RED
            row   = self._pos_rows[i]
            row[0].configure(text=pos.get("question", "")[:42])
            row[1].configure(text=pos.get("outcome", ""))
            row[2].configure(text=f"{pos.get('shares', 0):.3f}")
            row[3].configure(text=f"${pos.get('avg_entry_price', 0):.4f}")
            row[4].configure(text=f"${pos.get('current_price', 0):.4f}")
            row[5].configure(text=f"${pos.get('market_value', 0):.2f}")
            row[6].configure(text=f"{'+'if pnl>=0 else ''}{pnl:.2f}", text_color=color)
            row[7].configure(text=pos.get("expiry", "")[:10])

        # Update orders table
        orders = msg.get("open_orders", [])
        self._ensure_rows(self._ord_frame, self._ord_rows, len(orders), _ORD_COLS)

        now = time.time()
        for i, ord_ in enumerate(orders):
            age  = int(now - ord_.get("created_at", now))
            side = ord_.get("side", "")
            row  = self._ord_rows[i]
            row[0].configure(text=ord_.get("question", "")[:36])
            row[1].configure(text=side, text_color=GREEN if side == "BUY" else RED)
            row[2].configure(text=f"${ord_.get('size', 0):.2f}")
            row[3].configure(text=f"${ord_.get('price', 0):.4f}")
            row[4].configure(text=f"${ord_.get('filled_size', 0):.2f}")
            row[5].configure(text=ord_.get("status", ""))
            row[6].configure(text=ord_.get("strategy", ""))
            row[7].configure(text=f"{age}s")

    # ── Button handlers ────────────────────────────────────────────────────────
    def _export_csv(self) -> None:
        from core.position_manager import PositionManager
        try:
            path = PositionManager.instance().export_csv()
            from tkinter import messagebox
            messagebox.showinfo("Exported", f"P/L data saved to:\n{path}")
        except Exception as exc:
            log.error("CSV export failed: %s", exc)

    def _cancel_all(self) -> None:
        from tkinter import messagebox
        if messagebox.askyesno("Cancel All", "Cancel ALL open orders?"):
            from core.order_manager import OrderManager
            n = OrderManager.instance().cancel_all()
            messagebox.showinfo("Done", f"Cancelled {n} orders.")

    def _manual_refresh(self) -> None:
        from core.engine import BotEngine
        engine = BotEngine.instance()
        if engine.running:
            engine._push_gui_update()
