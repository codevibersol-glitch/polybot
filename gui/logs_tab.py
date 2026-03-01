"""
gui/logs_tab.py
───────────────
Logs & Controls Tab.

Layout
──────
  ┌── Log Viewer ────────────────────────────────────────────────────────────┐
  │  [Level: ALL ▼]  [Clear]  [Save Log]                                    │
  │  10:01:23  INFO  core.client  – Connected.  API key: abcd…              │
  │  10:01:25  INFO  strategies.market_making – Placing BUY …               │
  │  …                                                                       │
  └──────────────────────────────────────────────────────────────────────────┘
  ┌── Manual Order ──────────────────────────────────────────────────────────┐
  │  Token ID: [_______________]  Market ID: [_______________]               │
  │  Side: [BUY ▼]  Price: [______]  Size $: [______]  [Place Order]        │
  └──────────────────────────────────────────────────────────────────────────┘
  ┌── Emergency Controls ────────────────────────────────────────────────────┐
  │  [⚡ Cancel All & Flat]  [Stop Bot]  [Resume Bot]                        │
  └──────────────────────────────────────────────────────────────────────────┘
"""

from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from gui.app import PolybotApp

from utils.logger import get_logger

log = get_logger(__name__)

BG    = "#1a1a2e"
SURF  = "#16213e"
ACCENT = "#0f3460"
HILIT  = "#533483"
TEXT  = "#e0e0e0"
SUB   = "#9e9e9e"
GREEN = "#4caf50"
RED   = "#f44336"
YELLOW = "#ffc107"

# Log level tag colours (used by CTkTextbox – we simulate with text prefixes)
_LEVEL_COLORS = {
    "DEBUG":    SUB,
    "INFO":     TEXT,
    "WARNING":  YELLOW,
    "ERROR":    RED,
    "CRITICAL": RED,
}

_MAX_LOG_LINES = 2000   # keep last N lines to avoid memory growth


class LogsTab:
    """Log viewer + manual order panel + emergency controls."""

    def __init__(self, parent: ctk.CTkFrame, app: "PolybotApp") -> None:
        self.app = app
        self._log_lines: list[str] = []
        self._filter_level = "ALL"
        self._build(parent)

    def _build(self, parent: ctk.CTkFrame) -> None:
        parent.configure(fg_color=BG)

        # ── Log toolbar ────────────────────────────────────────────────────
        toolbar = ctk.CTkFrame(parent, fg_color=BG)
        toolbar.pack(fill="x", padx=12, pady=(8, 2))

        ctk.CTkLabel(toolbar, text="Level filter:", text_color=SUB).pack(side="left")
        self._level_var = ctk.StringVar(value="ALL")
        ctk.CTkOptionMenu(
            toolbar,
            variable=self._level_var,
            values=["ALL", "INFO", "WARNING", "ERROR"],
            command=self._on_level_change,
            fg_color=ACCENT, button_color=HILIT, width=110,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            toolbar, text="Clear", width=70,
            command=self._clear_log,
            fg_color=ACCENT, hover_color="#1a4a80",
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            toolbar, text="Save Log", width=90,
            command=self._save_log,
            fg_color=ACCENT, hover_color="#1a4a80",
        ).pack(side="left", padx=4)

        self._log_count_label = ctk.CTkLabel(
            toolbar, text="0 lines", text_color=SUB, font=ctk.CTkFont(size=10),
        )
        self._log_count_label.pack(side="right", padx=8)

        # ── Log text box ───────────────────────────────────────────────────
        self._log_box = ctk.CTkTextbox(
            parent,
            fg_color=SURF,
            text_color=TEXT,
            font=ctk.CTkFont(family="Courier New", size=11),
            wrap="none",
            state="disabled",
        )
        self._log_box.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        # ── Manual Order panel ─────────────────────────────────────────────
        order_frame = ctk.CTkFrame(parent, fg_color=SURF, corner_radius=8)
        order_frame.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(
            order_frame, text="Manual Order",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT,
        ).pack(anchor="w", padx=12, pady=(8, 4))

        row1 = ctk.CTkFrame(order_frame, fg_color=SURF)
        row1.pack(fill="x", padx=8, pady=2)

        ctk.CTkLabel(row1, text="Token ID:", text_color=SUB).pack(side="left", padx=4)
        self._token_entry = ctk.CTkEntry(row1, placeholder_text="0x…", width=280)
        self._token_entry.pack(side="left", padx=4)

        ctk.CTkLabel(row1, text="Market ID:", text_color=SUB).pack(side="left", padx=(16, 4))
        self._market_id_entry = ctk.CTkEntry(row1, placeholder_text="0x… (optional)", width=240)
        self._market_id_entry.pack(side="left", padx=4)

        row2 = ctk.CTkFrame(order_frame, fg_color=SURF)
        row2.pack(fill="x", padx=8, pady=(2, 8))

        ctk.CTkLabel(row2, text="Side:", text_color=SUB).pack(side="left", padx=4)
        self._side_var = ctk.StringVar(value="BUY")
        ctk.CTkOptionMenu(
            row2, variable=self._side_var,
            values=["BUY", "SELL"],
            fg_color=ACCENT, button_color=HILIT, width=80,
        ).pack(side="left", padx=4)

        ctk.CTkLabel(row2, text="Price (0-1):", text_color=SUB).pack(side="left", padx=(16, 4))
        self._price_entry = ctk.CTkEntry(row2, placeholder_text="0.65", width=80)
        self._price_entry.pack(side="left", padx=4)

        ctk.CTkLabel(row2, text="Size ($):", text_color=SUB).pack(side="left", padx=(16, 4))
        self._size_entry = ctk.CTkEntry(row2, placeholder_text="10.00", width=80)
        self._size_entry.pack(side="left", padx=4)

        ctk.CTkButton(
            row2, text="Place Order", width=120,
            command=self._place_manual_order,
            fg_color=HILIT, hover_color="#6a3d9a",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=16)

        # ── Emergency controls ─────────────────────────────────────────────
        emer = ctk.CTkFrame(parent, fg_color=BG)
        emer.pack(fill="x", padx=12, pady=6)

        ctk.CTkButton(
            emer, text="⚡  Cancel All & Flat", width=200,
            command=self._emergency_flat,
            fg_color="#7b1111", hover_color="#a01c1c",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            emer, text="⏹ Stop Bot", width=120,
            command=self._stop_bot,
            fg_color="#5a3311", hover_color="#7a4411",
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            emer, text="▶ Resume Bot", width=130,
            command=self._resume_bot,
            fg_color="#1a4a1a", hover_color="#246024",
        ).pack(side="left", padx=4)

    # ── Log ingestion (called by App._drain_gui_queue) ────────────────────────
    def append_log(self, level: str, text: str) -> None:
        """Add a log line to the viewer (call from main thread only)."""
        if self._filter_level != "ALL":
            order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            if order.index(level) < order.index(self._filter_level):
                return

        self._log_lines.append(f"[{level}] {text}")
        if len(self._log_lines) > _MAX_LOG_LINES:
            self._log_lines = self._log_lines[-_MAX_LOG_LINES:]

        # Prefix char to simulate colour in monospace box
        prefix = {"DEBUG": "·", "INFO": "»", "WARNING": "⚠", "ERROR": "✗", "CRITICAL": "‼"}.get(level, "»")
        line = f"{prefix} {text}\n"

        self._log_box.configure(state="normal")
        self._log_box.insert("end", line)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

        self._log_count_label.configure(text=f"{len(self._log_lines)} lines")

    def _on_level_change(self, value: str) -> None:
        self._filter_level = value
        # Rebuild display from stored lines
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        for line in self._log_lines:
            lvl = "INFO"
            for l in order:
                if f"[{l}]" in line[:12]:
                    lvl = l
                    break
            if value == "ALL" or order.index(lvl) >= order.index(value):
                self._log_box.insert("end", line + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log_lines = []
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._log_count_label.configure(text="0 lines")

    def _save_log(self) -> None:
        from tkinter import filedialog
        import time
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"polybot_log_{int(time.time())}.txt",
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._log_lines))
            log.info("Log saved to %s", path)

    # ── Manual order ──────────────────────────────────────────────────────────
    def _place_manual_order(self) -> None:
        from core.engine import BotEngine
        from tkinter import messagebox

        token_id  = self._token_entry.get().strip()
        market_id = self._market_id_entry.get().strip() or token_id
        side      = self._side_var.get()

        try:
            price = float(self._price_entry.get())
            size  = float(self._size_entry.get())
        except ValueError:
            messagebox.showerror("Invalid", "Price and Size must be numbers.")
            return

        if not token_id:
            messagebox.showerror("Invalid", "Token ID is required.")
            return

        engine = BotEngine.instance()
        if not engine.running:
            messagebox.showerror("Not Connected", "Connect the bot first (Setup tab).")
            return

        oid = engine.manual_order(
            token_id=token_id,
            market_id=market_id,
            question=f"Manual order on …{token_id[-12:]}",
            side=side,
            price=price,
            size=size,
        )
        if oid:
            messagebox.showinfo("Order Placed", f"Order ID: {oid}")
        else:
            messagebox.showerror("Failed", "Order was blocked by risk manager or CLOB error.")

    # ── Emergency controls ────────────────────────────────────────────────────
    def _emergency_flat(self) -> None:
        from tkinter import messagebox
        from core.order_manager import OrderManager

        if messagebox.askyesno(
            "Emergency Flat",
            "Cancel ALL open orders immediately?\nThis cannot be undone.",
        ):
            n = OrderManager.instance().cancel_all()
            messagebox.showinfo("Done", f"Cancelled {n} orders.\n\nNote: open POSITIONS still exist.")
            log.warning("EMERGENCY FLAT executed by user.")

    def _stop_bot(self) -> None:
        from tkinter import messagebox
        from core.engine import BotEngine
        if messagebox.askyesno("Stop Bot", "Stop the trading bot?\nAll strategies will pause."):
            BotEngine.instance().stop()
            messagebox.showinfo("Stopped", "Bot stopped. Reconnect via Setup tab.")

    def _resume_bot(self) -> None:
        from tkinter import messagebox
        messagebox.showinfo("Resume", "Use Setup tab → Connect & Go to restart the bot.")
