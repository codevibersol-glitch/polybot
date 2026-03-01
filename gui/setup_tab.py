"""
gui/setup_tab.py
────────────────
Setup Tab – credentials, allowance check, and "Connect & Go" button.

Layout
──────
  ┌── Credentials ────────────────────────────────────────────────────────────┐
  │  Private Key:      [●●●●●●●●●●●●●●●●●●●●●●●●●●●] [Show]                │
  │  Wallet Address:   [0x…]                                                 │
  │  Signature Type:   [EOA (0) ▼]                                           │
  │  [ ] Remember key (encrypted with OS keychain)                           │
  └───────────────────────────────────────────────────────────────────────────┘
  ┌── Allowance Status ───────────────────────────────────────────────────────┐
  │  USDC Allowance:    ✓ OK  / ✗ Not approved                               │
  │  Cond. Token:       ✓ OK  / ✗ Not approved                               │
  │  [ Check Allowances ]  [ Set Allowances (one-time) ]                     │
  └───────────────────────────────────────────────────────────────────────────┘
  [ Connect & Go ] ← big button                                               │
  Status: ● Not connected                                                      │
"""

import threading
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


class SetupTab:
    """Handles API credential entry, allowance checks, and initial connection."""

    def __init__(self, parent: ctk.CTkFrame, app: "PolybotApp") -> None:
        self.app = app
        self._connecting = False
        self._build(parent)
        self._restore_saved_credentials()

    def _build(self, parent: ctk.CTkFrame) -> None:
        parent.configure(fg_color=BG)

        scroll = ctk.CTkScrollableFrame(parent, fg_color=BG)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        # ── Credentials section ────────────────────────────────────────────
        cred_frame = ctk.CTkFrame(scroll, fg_color=SURF, corner_radius=8)
        cred_frame.pack(fill="x", padx=4, pady=8)

        ctk.CTkLabel(
            cred_frame, text="🔑  API Credentials",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT,
        ).pack(anchor="w", padx=16, pady=(12, 6))

        # Private key
        pk_row = ctk.CTkFrame(cred_frame, fg_color=SURF)
        pk_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(pk_row, text="Private Key:", width=130, anchor="w", text_color=TEXT).pack(side="left")
        self._pk_entry = ctk.CTkEntry(
            pk_row, width=440, show="●",
            placeholder_text="Paste your exported Polymarket private key here",
        )
        self._pk_entry.pack(side="left", padx=4)
        self._show_pk_btn = ctk.CTkButton(
            pk_row, text="Show", width=60,
            command=self._toggle_pk_visibility,
            fg_color=ACCENT, hover_color="#1a4a80",
        )
        self._show_pk_btn.pack(side="left", padx=4)

        # Wallet address
        wa_row = ctk.CTkFrame(cred_frame, fg_color=SURF)
        wa_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(wa_row, text="Wallet Address:", width=130, anchor="w", text_color=TEXT).pack(side="left")
        self._wallet_entry = ctk.CTkEntry(
            wa_row, width=440,
            placeholder_text="0x…  (your Polygon wallet / funder address)",
        )
        self._wallet_entry.pack(side="left", padx=4)

        # Signature type
        sig_row = ctk.CTkFrame(cred_frame, fg_color=SURF)
        sig_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(sig_row, text="Signature Type:", width=130, anchor="w", text_color=TEXT).pack(side="left")
        self._sig_var = ctk.StringVar(value="EOA (0) – standard wallet")
        ctk.CTkOptionMenu(
            sig_row,
            variable=self._sig_var,
            values=[
                "EOA (0) – standard wallet",
                "Gnosis Safe (1)",
                "Polymarket Magic (2)",
            ],
            fg_color=ACCENT, button_color=HILIT, width=280,
        ).pack(side="left", padx=4)

        # Remember key checkbox
        rem_row = ctk.CTkFrame(cred_frame, fg_color=SURF)
        rem_row.pack(fill="x", padx=12, pady=(4, 12))
        self._remember_var = ctk.BooleanVar(value=self.app.config.get("remember_key", False))
        ctk.CTkCheckBox(
            rem_row,
            text="Remember key (encrypted with OS keychain / AES-GCM file)",
            variable=self._remember_var,
            text_color=SUB,
        ).pack(side="left")

        # ── Allowances section ─────────────────────────────────────────────
        allow_frame = ctk.CTkFrame(scroll, fg_color=SURF, corner_radius=8)
        allow_frame.pack(fill="x", padx=4, pady=8)

        ctk.CTkLabel(
            allow_frame, text="✅  Token Allowances",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT,
        ).pack(anchor="w", padx=16, pady=(12, 4))

        ctk.CTkLabel(
            allow_frame,
            text="You need to approve USDC and Conditional Tokens for the CLOB exchange.\n"
                 "This is a one-time on-chain transaction (requires a small MATIC for gas).",
            text_color=SUB, font=ctk.CTkFont(size=11), wraplength=600,
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self._usdc_label   = ctk.CTkLabel(allow_frame, text="USDC Allowance:  —", text_color=SUB)
        self._usdc_label.pack(anchor="w", padx=16, pady=2)
        self._ctoken_label = ctk.CTkLabel(allow_frame, text="Cond. Token:     —", text_color=SUB)
        self._ctoken_label.pack(anchor="w", padx=16, pady=2)

        allow_btn_row = ctk.CTkFrame(allow_frame, fg_color=SURF)
        allow_btn_row.pack(anchor="w", padx=12, pady=(4, 12))

        ctk.CTkButton(
            allow_btn_row, text="Check Allowances", width=160,
            command=self._check_allowances,
            fg_color=ACCENT, hover_color="#1a4a80",
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            allow_btn_row, text="Set Allowances (one-time)", width=200,
            command=self._set_allowances,
            fg_color=HILIT, hover_color="#6a3d9a",
        ).pack(side="left", padx=4)

        # ── Connect button ─────────────────────────────────────────────────
        connect_frame = ctk.CTkFrame(scroll, fg_color=BG)
        connect_frame.pack(fill="x", padx=4, pady=16)

        self._connect_btn = ctk.CTkButton(
            connect_frame,
            text="🚀  Connect & Go",
            width=320, height=56,
            font=ctk.CTkFont(size=18, weight="bold"),
            command=self._connect,
            fg_color=GREEN, hover_color="#388e3c",
            text_color="#000000",
        )
        self._connect_btn.pack(pady=4)

        self._status_label = ctk.CTkLabel(
            connect_frame,
            text="● Not connected",
            font=ctk.CTkFont(size=13), text_color=RED,
        )
        self._status_label.pack(pady=4)

        # ── Help / tips ────────────────────────────────────────────────────
        help_frame = ctk.CTkFrame(scroll, fg_color=SURF, corner_radius=8)
        help_frame.pack(fill="x", padx=4, pady=8)

        tips = (
            "Quick Start:\n"
            "1. Export your private key from Polymarket → Settings → Export Key\n"
            "2. Copy your wallet address (the 0x… shown in Polymarket)\n"
            "3. Paste both above and click Connect & Go\n"
            "4. Open Markets tab → Refresh → find a market → click Auto-Trade\n"
            "5. Open Strategies tab → enable Market Making or Value Betting → Save\n"
            "6. Watch your positions update live on the Dashboard\n\n"
            "⚠  Your private key is NEVER logged or sent anywhere except directly to the "
            "official Polymarket CLOB API at clob.polymarket.com"
        )
        ctk.CTkLabel(
            help_frame, text=tips,
            text_color=SUB, font=ctk.CTkFont(size=11),
            justify="left", wraplength=700,
        ).pack(padx=16, pady=12, anchor="w")

    # ── Credential persistence ────────────────────────────────────────────────
    def _restore_saved_credentials(self) -> None:
        """Pre-fill wallet address from config; load key from keychain if saved."""
        wallet = self.app.config.get("wallet_address", "")
        if wallet:
            self._wallet_entry.insert(0, wallet)

        from utils.crypto import load_key, key_is_saved
        if key_is_saved():
            key = load_key()
            if key:
                self._pk_entry.insert(0, key)
                log.info("Private key loaded from secure storage.")

    # ── PK visibility toggle ──────────────────────────────────────────────────
    def _toggle_pk_visibility(self) -> None:
        current = self._pk_entry.cget("show")
        if current == "●":
            self._pk_entry.configure(show="")
            self._show_pk_btn.configure(text="Hide")
        else:
            self._pk_entry.configure(show="●")
            self._show_pk_btn.configure(text="Show")

    # ── Connection ────────────────────────────────────────────────────────────
    def _connect(self) -> None:
        if self._connecting:
            return

        private_key = self._pk_entry.get().strip()
        wallet      = self._wallet_entry.get().strip()
        _sig_map = {
            "EOA (0) – standard wallet": 0,
            "Gnosis Safe (1)":           1,
            "Polymarket Magic (2)":      2,
        }
        sig_type = _sig_map.get(self._sig_var.get(), 0)

        # Validate
        if not private_key:
            self._set_status("⚠ Please enter your private key.", YELLOW)
            return
        if not wallet or not wallet.startswith("0x"):
            self._set_status("⚠ Please enter a valid wallet address (0x…).", YELLOW)
            return

        # Save non-sensitive config
        self.app.config["wallet_address"] = wallet
        self.app.config["signature_type"] = sig_type
        self.app.config["remember_key"]   = self._remember_var.get()
        self.app.save_config()

        # Optionally store key
        if self._remember_var.get():
            from utils.crypto import save_key
            save_key(private_key)

        self._set_status("Connecting…", YELLOW)
        self._connect_btn.configure(state="disabled", text="Connecting…")
        self._connecting = True

        def _do_connect():
            try:
                from core.engine import BotEngine
                BotEngine.instance().connect(
                    private_key=private_key,
                    wallet=wallet,
                    sig_type=sig_type,
                    cfg=self.app.config,
                )
                # Success – notify on main thread
                self.app.gui_queue.put_nowait({"type": "connected", "wallet": wallet})
                # Load markets immediately
                from core.market_data import MarketDataService
                markets = MarketDataService.instance().fetch_markets(force=True)
                self.app.gui_queue.put_nowait({"type": "markets_loaded", "markets": markets})
            except Exception as exc:
                log.error("Connection failed: %s", exc, exc_info=True)
                self.app.gui_queue.put_nowait({
                    "type": "error",
                    "title": "Connection Failed",
                    "message": str(exc),
                })
                # Reset button state (must happen on main thread)
                self.app.after(0, self._reset_connect_btn)
            finally:
                self._connecting = False

        threading.Thread(target=_do_connect, daemon=True, name="ConnectThread").start()

    def _reset_connect_btn(self) -> None:
        self._connect_btn.configure(state="normal", text="🚀  Connect & Go")
        self._set_status("● Not connected", RED)

    def on_connected(self) -> None:
        """Called by App._on_connected() on the main thread."""
        wallet = self.app.config.get("wallet_address", "")[:12]
        self._set_status(f"● Connected  ({wallet}…)", GREEN)
        self._connect_btn.configure(
            state="normal", text="✓  Connected",
            fg_color=ACCENT, hover_color=ACCENT,
        )

    def _set_status(self, text: str, color: str) -> None:
        self._status_label.configure(text=text, text_color=color)

    # ── Allowances ────────────────────────────────────────────────────────────
    def _check_allowances(self) -> None:
        from core.client import PolyClient
        if not PolyClient.instance().connected:
            from tkinter import messagebox
            messagebox.showwarning("Not Connected", "Connect first, then check allowances.")
            return

        def _fetch():
            data = PolyClient.instance().check_allowances()
            self.app.after(0, lambda: self._display_allowances(data))

        threading.Thread(target=_fetch, daemon=True).start()

    def _display_allowances(self, data: dict) -> None:
        usdc   = data.get("usdc", {})
        ctoken = data.get("ctoken", {})

        def _fmt(d) -> str:
            if not d:
                return "✗  Not set"
            allowance = d.get("allowance", 0)
            try:
                amt = float(allowance)
                if amt > 1e6:
                    return f"✓  {amt/1e6:.1f}M"
                return f"✓  {amt:,.0f}"
            except Exception:
                return f"✓  {allowance}"

        self._usdc_label.configure(
            text=f"USDC Allowance:   {_fmt(usdc)}",
            text_color=GREEN if usdc else RED,
        )
        self._ctoken_label.configure(
            text=f"Cond. Token:      {_fmt(ctoken)}",
            text_color=GREEN if ctoken else RED,
        )

    def _set_allowances(self) -> None:
        from core.client import PolyClient
        from tkinter import messagebox

        if not PolyClient.instance().connected:
            messagebox.showwarning("Not Connected", "Connect first, then set allowances.")
            return

        if not messagebox.askyesno(
            "Set Allowances",
            "This will submit blockchain transactions to approve USDC and Conditional Tokens "
            "for the Polymarket CLOB exchange.\n\n"
            "You will need a small amount of MATIC in your wallet for gas fees.\n\n"
            "Continue?",
        ):
            return

        def _do():
            try:
                PolyClient.instance().set_allowances()
                self.app.after(0, lambda: messagebox.showinfo(
                    "Done", "Allowances set! You can now trade."
                ))
                # Refresh allowance display
                self._check_allowances()
            except Exception as exc:
                log.error("set_allowances failed: %s", exc)
                self.app.after(0, lambda: messagebox.showerror(
                    "Error", f"Failed to set allowances:\n{exc}"
                ))

        threading.Thread(target=_do, daemon=True).start()
