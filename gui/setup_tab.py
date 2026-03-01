"""
gui/setup_tab.py
────────────────
Setup Tab – credentials, allowance check, and "Connect & Go" button.

Layout
──────
  ┌── Credentials ────────────────────────────────────────────────────────────┐
  │  [Private Key]  [API Credentials]  ← segmented toggle                   │
  │                                                                           │
  │  — Private Key mode ──────────────────────────────────────────────────  │
  │  Private Key:      [●●●●●●●●●●●●●●●●●●●●●●●●●●●] [Show]               │
  │  Signature Type:   [EOA (0) ▼]                                          │
  │                                                                           │
  │  — API Credentials mode ───────────────────────────────────────────────  │
  │  API Key:          [019caa24-…]                                          │
  │  API Secret:       [●●●●●●●●●●●●●●●●●●●●●●●●●●●] [Show]               │
  │  API Passphrase:   [●●●●●●●●●●●●●●●●●●●●●●●●●●●] [Show]               │
  │                                                                           │
  │  Wallet Address:   [0x…]                                                 │
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

        # Auth mode toggle
        self._auth_mode_var = ctk.StringVar(
            value="Private Key" if self.app.config.get("auth_mode", "private_key") == "private_key"
            else "API Credentials"
        )
        ctk.CTkSegmentedButton(
            cred_frame,
            values=["Private Key", "API Credentials"],
            variable=self._auth_mode_var,
            command=self._on_auth_mode_change,
            fg_color=ACCENT,
            selected_color=HILIT,
            selected_hover_color="#6a3d9a",
            unselected_color=ACCENT,
        ).pack(anchor="w", padx=16, pady=(0, 10))

        # ── Private Key panel ──────────────────────────────────────────────
        self._pk_panel = ctk.CTkFrame(cred_frame, fg_color=SURF)

        pk_row = ctk.CTkFrame(self._pk_panel, fg_color=SURF)
        pk_row.pack(fill="x", padx=0, pady=4)
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

        sig_row = ctk.CTkFrame(self._pk_panel, fg_color=SURF)
        sig_row.pack(fill="x", padx=0, pady=4)
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

        # ── API Credentials panel ──────────────────────────────────────────
        self._api_panel = ctk.CTkFrame(cred_frame, fg_color=SURF)

        ak_row = ctk.CTkFrame(self._api_panel, fg_color=SURF)
        ak_row.pack(fill="x", padx=0, pady=4)
        ctk.CTkLabel(ak_row, text="API Key:", width=130, anchor="w", text_color=TEXT).pack(side="left")
        self._api_key_entry = ctk.CTkEntry(
            ak_row, width=440,
            placeholder_text="019caa24-50ee-… (your Polymarket API key)",
        )
        self._api_key_entry.pack(side="left", padx=4)

        asec_row = ctk.CTkFrame(self._api_panel, fg_color=SURF)
        asec_row.pack(fill="x", padx=0, pady=4)
        ctk.CTkLabel(asec_row, text="API Secret:", width=130, anchor="w", text_color=TEXT).pack(side="left")
        self._api_secret_entry = ctk.CTkEntry(
            asec_row, width=440, show="●",
            placeholder_text="Base64-encoded secret from Polymarket settings",
        )
        self._api_secret_entry.pack(side="left", padx=4)
        self._show_secret_btn = ctk.CTkButton(
            asec_row, text="Show", width=60,
            command=self._toggle_secret_visibility,
            fg_color=ACCENT, hover_color="#1a4a80",
        )
        self._show_secret_btn.pack(side="left", padx=4)

        apass_row = ctk.CTkFrame(self._api_panel, fg_color=SURF)
        apass_row.pack(fill="x", padx=0, pady=4)
        ctk.CTkLabel(apass_row, text="API Passphrase:", width=130, anchor="w", text_color=TEXT).pack(side="left")
        self._api_pass_entry = ctk.CTkEntry(
            apass_row, width=440, show="●",
            placeholder_text="Passphrase from Polymarket settings",
        )
        self._api_pass_entry.pack(side="left", padx=4)
        self._show_pass_btn = ctk.CTkButton(
            apass_row, text="Show", width=60,
            command=self._toggle_pass_visibility,
            fg_color=ACCENT, hover_color="#1a4a80",
        )
        self._show_pass_btn.pack(side="left", padx=4)

        # ── Shared fields (always visible) ────────────────────────────────
        wa_row = ctk.CTkFrame(cred_frame, fg_color=SURF)
        wa_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(wa_row, text="Wallet Address:", width=130, anchor="w", text_color=TEXT).pack(side="left")
        self._wallet_entry = ctk.CTkEntry(
            wa_row, width=440,
            placeholder_text="0x…  (your Polygon wallet / funder address)",
        )
        self._wallet_entry.pack(side="left", padx=4)

        rem_row = ctk.CTkFrame(cred_frame, fg_color=SURF)
        rem_row.pack(fill="x", padx=12, pady=(4, 12))
        self._remember_var = ctk.BooleanVar(value=self.app.config.get("remember_key", False))
        ctk.CTkCheckBox(
            rem_row,
            text="Remember key (encrypted with OS keychain / AES-GCM file)",
            variable=self._remember_var,
            text_color=SUB,
        ).pack(side="left")

        # Show correct panel for initial mode
        self._on_auth_mode_change(self._auth_mode_var.get(), pack_parent_padx=12)

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
            "Option A – Private Key:  Export your key from Polymarket → Settings → Export Key\n"
            "Option B – API Credentials:  Copy apiKey, secret, passphrase from Polymarket → Settings → API Keys\n\n"
            "1. Enter your wallet address (0x… shown in Polymarket)\n"
            "2. Choose auth mode and paste credentials above\n"
            "3. Click Connect & Go\n"
            "4. Open Markets tab → Refresh → find a market → click Auto-Trade\n"
            "5. Open Strategies tab → enable a strategy → Save\n"
            "6. Watch your positions update live on the Dashboard\n\n"
            "⚠  Your credentials are NEVER logged or sent anywhere except directly to the "
            "official Polymarket CLOB API at clob.polymarket.com"
        )
        ctk.CTkLabel(
            help_frame, text=tips,
            text_color=SUB, font=ctk.CTkFont(size=11),
            justify="left", wraplength=700,
        ).pack(padx=16, pady=12, anchor="w")

    # ── Auth mode toggle ──────────────────────────────────────────────────────
    def _on_auth_mode_change(self, mode: str, pack_parent_padx: int = 12) -> None:
        if mode == "Private Key":
            self._api_panel.pack_forget()
            self._pk_panel.pack(fill="x", padx=pack_parent_padx, pady=(0, 4))
        else:
            self._pk_panel.pack_forget()
            self._api_panel.pack(fill="x", padx=pack_parent_padx, pady=(0, 4))

    # ── Credential persistence ────────────────────────────────────────────────
    def _restore_saved_credentials(self) -> None:
        """Pre-fill wallet address from config; restore saved credentials."""
        wallet = self.app.config.get("wallet_address", "")
        if wallet:
            self._wallet_entry.insert(0, wallet)

        from utils.crypto import load_key, key_is_saved

        mode = self.app.config.get("auth_mode", "private_key")
        if mode == "api_creds":
            # Restore API key (non-sensitive) from config
            ak = self.app.config.get("api_key", "")
            if ak:
                self._api_key_entry.insert(0, ak)
            # Restore secret+passphrase from secure storage (stored as "secret:::passphrase")
            if key_is_saved():
                stored = load_key()
                if stored and ":::" in stored:
                    secret, passphrase = stored.split(":::", 1)
                    self._api_secret_entry.insert(0, secret)
                    self._api_pass_entry.insert(0, passphrase)
                    log.info("API credentials loaded from secure storage.")
                elif stored:
                    # Legacy: only secret stored
                    self._api_secret_entry.insert(0, stored)
        else:
            # Private key mode
            if key_is_saved():
                key = load_key()
                if key:
                    self._pk_entry.insert(0, key)
                    log.info("Private key loaded from secure storage.")

    # ── Visibility toggles ────────────────────────────────────────────────────
    def _toggle_pk_visibility(self) -> None:
        current = self._pk_entry.cget("show")
        if current == "●":
            self._pk_entry.configure(show="")
            self._show_pk_btn.configure(text="Hide")
        else:
            self._pk_entry.configure(show="●")
            self._show_pk_btn.configure(text="Show")

    def _toggle_secret_visibility(self) -> None:
        current = self._api_secret_entry.cget("show")
        if current == "●":
            self._api_secret_entry.configure(show="")
            self._show_secret_btn.configure(text="Hide")
        else:
            self._api_secret_entry.configure(show="●")
            self._show_secret_btn.configure(text="Show")

    def _toggle_pass_visibility(self) -> None:
        current = self._api_pass_entry.cget("show")
        if current == "●":
            self._api_pass_entry.configure(show="")
            self._show_pass_btn.configure(text="Hide")
        else:
            self._api_pass_entry.configure(show="●")
            self._show_pass_btn.configure(text="Show")

    # ── Connection ────────────────────────────────────────────────────────────
    def _connect(self) -> None:
        if self._connecting:
            return

        mode   = self._auth_mode_var.get()
        wallet = self._wallet_entry.get().strip()

        if not wallet or not wallet.startswith("0x"):
            self._set_status("⚠ Please enter a valid wallet address (0x…).", YELLOW)
            return

        if mode == "API Credentials":
            self._connect_api_creds(wallet)
        else:
            self._connect_private_key(wallet)

    def _connect_private_key(self, wallet: str) -> None:
        private_key = self._pk_entry.get().strip()
        _sig_map = {
            "EOA (0) – standard wallet": 0,
            "Gnosis Safe (1)":           1,
            "Polymarket Magic (2)":      2,
        }
        sig_type = _sig_map.get(self._sig_var.get(), 0)

        if not private_key:
            self._set_status("⚠ Please enter your private key.", YELLOW)
            return

        self.app.config["wallet_address"] = wallet
        self.app.config["signature_type"] = sig_type
        self.app.config["remember_key"]   = self._remember_var.get()
        self.app.config["auth_mode"]      = "private_key"
        self.app.save_config()

        if self._remember_var.get():
            from utils.crypto import save_key
            save_key(private_key)

        self._start_connection_thread(
            private_key=private_key,
            wallet=wallet,
            sig_type=sig_type,
            api_creds=None,
        )

    def _connect_api_creds(self, wallet: str) -> None:
        api_key        = self._api_key_entry.get().strip()
        api_secret     = self._api_secret_entry.get().strip()
        api_passphrase = self._api_pass_entry.get().strip()

        if not api_key:
            self._set_status("⚠ Please enter your API Key.", YELLOW)
            return
        if not api_secret:
            self._set_status("⚠ Please enter your API Secret.", YELLOW)
            return
        if not api_passphrase:
            self._set_status("⚠ Please enter your API Passphrase.", YELLOW)
            return

        self.app.config["wallet_address"] = wallet
        self.app.config["remember_key"]   = self._remember_var.get()
        self.app.config["auth_mode"]      = "api_creds"
        self.app.config["api_key"]        = api_key
        self.app.save_config()

        if self._remember_var.get():
            from utils.crypto import save_key
            # Store secret and passphrase together, split on :::
            save_key(f"{api_secret}:::{api_passphrase}")

        self._start_connection_thread(
            private_key="",
            wallet=wallet,
            sig_type=2,
            api_creds={
                "api_key":        api_key,
                "api_secret":     api_secret,
                "api_passphrase": api_passphrase,
            },
        )

    def _start_connection_thread(
        self,
        private_key: str,
        wallet: str,
        sig_type: int,
        api_creds: "dict | None",
    ) -> None:
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
                    api_creds=api_creds,
                )
                self.app.gui_queue.put_nowait({"type": "connected", "wallet": wallet})
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
                self._check_allowances()
            except Exception as exc:
                log.error("set_allowances failed: %s", exc)
                self.app.after(0, lambda: messagebox.showerror(
                    "Error", f"Failed to set allowances:\n{exc}"
                ))

        threading.Thread(target=_do, daemon=True).start()
