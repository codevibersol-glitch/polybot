"""
gui/app.py
──────────
Main application window (PolybotApp).

Layout
──────
  ┌─────────────────────────────────────────────────┐
  │  PolyBot  [Dashboard] [Markets] [Strat] [Logs] [Setup]  │
  │─────────────────────────────────────────────────│
  │  ← selected tab content →                       │
  │─────────────────────────────────────────────────│
  │  Status bar: Connected ● | Portfolio $123.45     │
  └─────────────────────────────────────────────────┘

The GUI update loop
────────────────────
  App.after(200, _drain_gui_queue) drains the engine's gui_queue 5×/sec,
  applying status updates to labels/tables without blocking the engine.
"""

import queue
import threading
import tkinter as tk
from typing import Optional

import customtkinter as ctk

from utils.logger import get_logger, set_gui_queue
from utils.config import load as load_config, save as save_config
from core.engine import BotEngine

log = get_logger(__name__)

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Colour palette used across all tabs
PALETTE = {
    "bg":         "#1a1a2e",   # deep navy background
    "surface":    "#16213e",   # card / panel background
    "accent":     "#0f3460",   # accent / header rows
    "highlight":  "#533483",   # interactive highlight
    "green":      "#4caf50",
    "red":        "#f44336",
    "yellow":     "#ffc107",
    "text":       "#e0e0e0",
    "subtext":    "#9e9e9e",
}


class PolybotApp(ctk.CTk):
    """
    Root application window.

    Instantiate and call .mainloop() from main.py.
    """

    def __init__(self) -> None:
        super().__init__()

        self.title("PolyBot – Polymarket Automated Trader")
        self.geometry("1280x800")
        self.minsize(1024, 680)
        self.configure(fg_color=PALETTE["bg"])

        # ── Shared state ──────────────────────────────────────────────────────
        self.config: dict = load_config()
        self.gui_queue: queue.Queue = queue.Queue(maxsize=2000)
        self.engine   = BotEngine.instance()
        self.engine.gui_queue = self.gui_queue

        # Register GUI queue with logger so log messages appear in Logs tab
        set_gui_queue(self.gui_queue)

        # ── Build UI ──────────────────────────────────────────────────────────
        self._build_header()
        self._build_tabs()
        self._build_statusbar()

        # ── Graceful shutdown ─────────────────────────────────────────────────
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Start GUI update loop ─────────────────────────────────────────────
        self.after(200, self._drain_gui_queue)

        log.info("PolyBot GUI ready.")

    # ── Header ────────────────────────────────────────────────────────────────
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, fg_color=PALETTE["accent"], height=48, corner_radius=0)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        ctk.CTkLabel(
            hdr,
            text="⬡  PolyBot",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#ffffff",
        ).pack(side="left", padx=20)

        # Connection indicator (updated from status bar logic)
        self._conn_label = ctk.CTkLabel(
            hdr,
            text="● Disconnected",
            font=ctk.CTkFont(size=12),
            text_color=PALETTE["red"],
        )
        self._conn_label.pack(side="right", padx=20)

    # ── Tabs ──────────────────────────────────────────────────────────────────
    def _build_tabs(self) -> None:
        self._tabview = ctk.CTkTabview(
            self,
            fg_color=PALETTE["surface"],
            segmented_button_fg_color=PALETTE["accent"],
            segmented_button_selected_color=PALETTE["highlight"],
            segmented_button_unselected_color=PALETTE["accent"],
            segmented_button_selected_hover_color="#6a3d9a",
        )
        self._tabview.pack(fill="both", expand=True, padx=8, pady=(4, 4))

        # Create tab frames
        for tab_name in ["Dashboard", "Markets", "Strategies", "Logs", "Setup"]:
            self._tabview.add(tab_name)

        # Lazy-import tab modules to avoid circular imports
        from gui.dashboard_tab   import DashboardTab
        from gui.markets_tab     import MarketsTab
        from gui.strategies_tab  import StrategiesTab
        from gui.logs_tab        import LogsTab
        from gui.setup_tab       import SetupTab

        self.dashboard_tab  = DashboardTab(self._tabview.tab("Dashboard"),   self)
        self.markets_tab    = MarketsTab(self._tabview.tab("Markets"),       self)
        self.strategies_tab = StrategiesTab(self._tabview.tab("Strategies"), self)
        self.logs_tab       = LogsTab(self._tabview.tab("Logs"),             self)
        self.setup_tab      = SetupTab(self._tabview.tab("Setup"),           self)

    # ── Status bar ────────────────────────────────────────────────────────────
    def _build_statusbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=PALETTE["accent"], height=28, corner_radius=0)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self._status_portfolio = ctk.CTkLabel(
            bar, text="Portfolio: --",
            font=ctk.CTkFont(size=11), text_color=PALETTE["text"],
        )
        self._status_portfolio.pack(side="left", padx=12)

        self._status_pnl = ctk.CTkLabel(
            bar, text="Total P/L: --",
            font=ctk.CTkFont(size=11), text_color=PALETTE["text"],
        )
        self._status_pnl.pack(side="left", padx=12)

        self._status_risk = ctk.CTkLabel(
            bar, text="Exposure: --",
            font=ctk.CTkFont(size=11), text_color=PALETTE["subtext"],
        )
        self._status_risk.pack(side="right", padx=12)

    # ── GUI queue drain ────────────────────────────────────────────────────────
    def _drain_gui_queue(self) -> None:
        """
        Called by Tkinter's event loop every 200 ms.
        Processes up to 20 messages per drain to avoid UI stuttering.
        """
        try:
            for _ in range(20):
                try:
                    msg = self.gui_queue.get_nowait()
                except queue.Empty:
                    break

                if isinstance(msg, tuple):
                    # (levelname, log_string) from logger
                    level, text = msg
                    self.logs_tab.append_log(level, text)

                elif isinstance(msg, dict):
                    mtype = msg.get("type")
                    if mtype == "status":
                        self._apply_status(msg)
                    elif mtype == "connected":
                        self._on_connected(msg)
                    elif mtype == "error":
                        self._show_error(msg.get("title", "Error"), msg.get("message", ""))
                    elif mtype == "markets_loaded":
                        self.markets_tab.refresh_table(msg.get("markets", []))

        finally:
            # Reschedule – always keep the drain loop alive
            self.after(200, self._drain_gui_queue)

    def _apply_status(self, msg: dict) -> None:
        """Update status bar and dashboard with latest engine snapshot."""
        pv   = msg.get("portfolio_value", 0)
        pnl  = msg.get("total_pnl", 0)
        exp  = msg.get("risk", {}).get("total_exposure", 0)
        halted = msg.get("risk", {}).get("halted", False)

        self._status_portfolio.configure(text=f"Portfolio: ${pv:,.2f}")

        pnl_color = PALETTE["green"] if pnl >= 0 else PALETTE["red"]
        self._status_pnl.configure(
            text=f"P/L: {'+'if pnl>=0 else ''}{pnl:,.2f}",
            text_color=pnl_color,
        )

        risk_text = f"Exposure: ${exp:,.0f}"
        if halted:
            risk_text = "⚠ RISK HALT"
        self._status_risk.configure(
            text=risk_text,
            text_color=PALETTE["red"] if halted else PALETTE["subtext"],
        )

        # Forward to dashboard tab
        self.dashboard_tab.update_status(msg)

    def _on_connected(self, msg: dict) -> None:
        wallet = msg.get("wallet", "")[:10]
        self._conn_label.configure(
            text=f"● Connected ({wallet}…)", text_color=PALETTE["green"]
        )
        self.setup_tab.on_connected()

    def _show_error(self, title: str, message: str) -> None:
        from tkinter import messagebox
        messagebox.showerror(title, message)

    # ── Called by Setup tab after successful connection ───────────────────────
    def notify_connected(self, wallet: str) -> None:
        self.gui_queue.put_nowait({"type": "connected", "wallet": wallet})

    def notify_error(self, title: str, message: str) -> None:
        self.gui_queue.put_nowait({"type": "error", "title": title, "message": message})

    # ── Config persistence ────────────────────────────────────────────────────
    def save_config(self) -> None:
        """Merge and save current config (called from tabs)."""
        save_config(self.config)

    # ── Shutdown ──────────────────────────────────────────────────────────────
    def _on_close(self) -> None:
        log.info("Closing application…")
        try:
            if self.engine.running:
                self.engine.stop()
        except Exception as exc:
            log.error("Error during shutdown: %s", exc)
        self.save_config()
        self.destroy()
