"""
gui/strategies_tab.py
─────────────────────
Strategies Tab – configure all four trading strategies and global risk limits.

Layout
──────
  ┌── Global Risk Limits ─────────────────────────────────────┐
  │  Max Total Exposure  Max Per Market  Daily Loss Stop       │
  └───────────────────────────────────────────────────────────┘
  ┌── Market Making ──── [Enabled ☑] ────────────────────────┐
  │  Spread %   Max Position $   Refresh secs   Max Markets   │
  └───────────────────────────────────────────────────────────┘
  ┌── Value Betting ─── [Enabled ☑] ────────────────────────-┐
  │  Min Edge %   Max Position $   Kelly Fraction             │
  │  Fair-value overrides table (token_id → prob)             │
  └───────────────────────────────────────────────────────────┘
  ┌── Copy Trading ──── [Enabled ☑] ─────────────────────────┐
  │  Scale Factor   Max Position $   Check Interval (min)     │
  └───────────────────────────────────────────────────────────┘
  ┌── Time Decay ──────[Enabled ☑] ─────────────────────────-┐
  │  Hours Before Expiry   Min No Price                        │
  └───────────────────────────────────────────────────────────┘
  [ Save All ]
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


def _float_entry(parent, label: str, var: ctk.StringVar, width: int = 90) -> None:
    """Helper: label + entry widget pair."""
    ctk.CTkLabel(parent, text=label, text_color=SUB, font=ctk.CTkFont(size=11)).pack(
        side="left", padx=(12, 2)
    )
    ctk.CTkEntry(parent, textvariable=var, width=width).pack(side="left", padx=(0, 8))


class StrategiesTab:
    """Builds and manages the Strategies tab."""

    def __init__(self, parent: ctk.CTkFrame, app: "PolybotApp") -> None:
        self.app = app
        self._vars: dict[str, ctk.Variable] = {}
        self._build(parent)
        self._load_from_config()

    def _build(self, parent: ctk.CTkFrame) -> None:
        parent.configure(fg_color=BG)

        scroll = ctk.CTkScrollableFrame(parent, fg_color=BG)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_risk_section(scroll)
        self._build_mm_section(scroll)
        self._build_vb_section(scroll)
        self._build_ct_section(scroll)
        self._build_td_section(scroll)

        # Save button
        ctk.CTkButton(
            scroll, text="💾  Save All Strategies & Risk", width=260,
            command=self._save_all,
            fg_color=HILIT, hover_color="#6a3d9a",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(pady=16)

    # ── Section builder helper ────────────────────────────────────────────────
    def _section(self, parent, title: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=SURF, corner_radius=8)
        frame.pack(fill="x", padx=4, pady=6)
        ctk.CTkLabel(
            frame, text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=12, pady=(8, 4))
        return frame

    def _row(self, parent) -> ctk.CTkFrame:
        r = ctk.CTkFrame(parent, fg_color=SURF)
        r.pack(fill="x", pady=2)
        return r

    def _enabled_switch(self, parent, key: str) -> ctk.CTkSwitch:
        var = ctk.BooleanVar(value=False)
        self._vars[key] = var
        sw = ctk.CTkSwitch(
            parent, text="Enabled",
            variable=var, onvalue=True, offvalue=False,
            progress_color=GREEN,
        )
        sw.pack(side="right", padx=12, pady=4)
        return sw

    def _str_var(self, key: str, default: str = "0") -> ctk.StringVar:
        v = ctk.StringVar(value=default)
        self._vars[key] = v
        return v

    # ── Global Risk ────────────────────────────────────────────────────────────
    def _build_risk_section(self, parent) -> None:
        sec = self._section(parent, "⚠  Global Risk Limits")
        row = self._row(sec)

        _float_entry(row, "Max Total Exposure ($)",
                     self._str_var("risk.max_total_exposure_usd", "200"), 90)
        _float_entry(row, "Max Per Market ($)",
                     self._str_var("risk.max_per_market_usd", "50"), 90)
        _float_entry(row, "Daily Loss Stop ($)",
                     self._str_var("risk.max_daily_loss_usd", "50"), 90)

        ctk.CTkButton(
            sec, text="Resume After Halt", width=160,
            command=self._resume_risk,
            fg_color="#7b1111", hover_color="#a01c1c",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=12, pady=(4, 8))

    # ── Market Making ─────────────────────────────────────────────────────────
    def _build_mm_section(self, parent) -> None:
        sec = self._section(parent, "📊  Market Making")
        self._enabled_switch(sec, "mm.enabled")

        ctk.CTkLabel(
            sec,
            text="Provides two-sided quotes around the order-book midpoint with volatility-adjusted spread.",
            font=ctk.CTkFont(size=10), text_color=SUB, wraplength=700,
        ).pack(anchor="w", padx=12)

        row = self._row(sec)
        _float_entry(row, "Base Spread %",         self._str_var("mm.spread_pct", "2"), 70)
        _float_entry(row, "Max Position ($)",      self._str_var("mm.max_pos_usd", "50"), 70)
        _float_entry(row, "Refresh (secs)",        self._str_var("mm.refresh_sec", "10"), 60)
        _float_entry(row, "Max Markets",           self._str_var("mm.max_markets", "5"), 50)

        ctk.CTkLabel(sec, text="", height=4).pack()  # spacer

    # ── Value Betting ─────────────────────────────────────────────────────────
    def _build_vb_section(self, parent) -> None:
        sec = self._section(parent, "🎯  Value Betting / Probability Edge")
        self._enabled_switch(sec, "vb.enabled")

        ctk.CTkLabel(
            sec,
            text="Bets when implied probability deviates from our fair-value estimate by more than the edge threshold.",
            font=ctk.CTkFont(size=10), text_color=SUB, wraplength=700,
        ).pack(anchor="w", padx=12)

        row = self._row(sec)
        _float_entry(row, "Min Edge %",      self._str_var("vb.min_edge_pct", "5"), 60)
        _float_entry(row, "Max Pos ($)",     self._str_var("vb.max_pos_usd", "30"), 70)
        _float_entry(row, "Kelly Fraction",  self._str_var("vb.kelly_fraction", "0.25"), 70)

        # Fair value override
        ctk.CTkLabel(sec, text="Fair-Value Override (token_id → probability 0-1):",
                     text_color=SUB, font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=12, pady=(6, 2))

        fv_row = ctk.CTkFrame(sec, fg_color=SURF)
        fv_row.pack(fill="x", padx=12, pady=4)

        self._fv_token_entry = ctk.CTkEntry(fv_row, placeholder_text="Token ID (0x…)", width=280)
        self._fv_token_entry.pack(side="left", padx=4)

        self._fv_prob_entry = ctk.CTkEntry(fv_row, placeholder_text="Fair prob (e.g. 0.70)", width=140)
        self._fv_prob_entry.pack(side="left", padx=4)

        ctk.CTkButton(
            fv_row, text="Set", width=60,
            command=self._set_fair_value,
            fg_color=ACCENT,
        ).pack(side="left", padx=4)

        ctk.CTkLabel(sec, text="", height=4).pack()

    # ── Copy Trading ──────────────────────────────────────────────────────────
    def _build_ct_section(self, parent) -> None:
        sec = self._section(parent, "🔁  Copy Trading Lite")
        self._enabled_switch(sec, "ct.enabled")

        ctk.CTkLabel(
            sec,
            text="Mirrors trades of top-volume Polymarket traders at a scaled-down size.",
            font=ctk.CTkFont(size=10), text_color=SUB, wraplength=700,
        ).pack(anchor="w", padx=12)

        row = self._row(sec)
        _float_entry(row, "Scale Factor (0-1)",    self._str_var("ct.scale_factor", "0.1"), 70)
        _float_entry(row, "Max Pos ($)",           self._str_var("ct.max_pos_usd", "20"), 70)
        _float_entry(row, "Check Interval (min)",  self._str_var("ct.check_interval_min", "5"), 60)

        ctk.CTkLabel(sec, text="", height=4).pack()

    # ── Time Decay ────────────────────────────────────────────────────────────
    def _build_td_section(self, parent) -> None:
        sec = self._section(parent, "⏳  Time-Decay Auto-Sell")
        self._enabled_switch(sec, "td.enabled")

        ctk.CTkLabel(
            sec,
            text="Liquidates positions near market expiry and buys cheap 'No' tokens on stale markets.",
            font=ctk.CTkFont(size=10), text_color=SUB, wraplength=700,
        ).pack(anchor="w", padx=12)

        row = self._row(sec)
        _float_entry(row, "Hours Before Expiry", self._str_var("td.hours_before_expiry", "6"), 60)
        _float_entry(row, "Min No Price",        self._str_var("td.min_no_price", "0.85"), 70)

        ctk.CTkLabel(sec, text="", height=4).pack()

    # ── Load from config ──────────────────────────────────────────────────────
    def _load_from_config(self) -> None:
        cfg   = self.app.config
        risk  = cfg.get("risk", {})
        strat = cfg.get("strategies", {})
        mm    = strat.get("market_making", {})
        vb    = strat.get("value_betting", {})
        ct    = strat.get("copy_trading", {})
        td    = strat.get("time_decay", {})

        def _set(key: str, val) -> None:
            v = self._vars.get(key)
            if v:
                if isinstance(v, ctk.BooleanVar):
                    v.set(bool(val))
                else:
                    v.set(str(val))

        _set("risk.max_total_exposure_usd", risk.get("max_total_exposure_usd", 200))
        _set("risk.max_per_market_usd",     risk.get("max_per_market_usd", 50))
        _set("risk.max_daily_loss_usd",     risk.get("max_daily_loss_usd", 50))

        _set("mm.enabled",    mm.get("enabled", False))
        _set("mm.spread_pct", mm.get("spread_pct", 0.02) * 100)   # store as %
        _set("mm.max_pos_usd", mm.get("max_pos_usd", 50))
        _set("mm.refresh_sec", mm.get("refresh_sec", 10))
        _set("mm.max_markets", mm.get("max_markets", 5))

        _set("vb.enabled",        vb.get("enabled", False))
        _set("vb.min_edge_pct",   vb.get("min_edge_pct", 0.05) * 100)
        _set("vb.max_pos_usd",    vb.get("max_pos_usd", 30))
        _set("vb.kelly_fraction", vb.get("kelly_fraction", 0.25))

        _set("ct.enabled",              ct.get("enabled", False))
        _set("ct.scale_factor",         ct.get("scale_factor", 0.10))
        _set("ct.max_pos_usd",          ct.get("max_pos_usd", 20))
        _set("ct.check_interval_min",   ct.get("check_interval_min", 5))

        _set("td.enabled",               td.get("enabled", False))
        _set("td.hours_before_expiry",   td.get("hours_before_expiry", 6))
        _set("td.min_no_price",          td.get("min_no_price", 0.85))

    # ── Save all ──────────────────────────────────────────────────────────────
    def _save_all(self) -> None:
        def _f(key: str, default=0.0) -> float:
            try:
                return float(self._vars[key].get())
            except Exception:
                return default

        def _b(key: str) -> bool:
            try:
                return bool(self._vars[key].get())
            except Exception:
                return False

        cfg = self.app.config
        cfg["risk"] = {
            "max_total_exposure_usd": _f("risk.max_total_exposure_usd", 200),
            "max_per_market_usd":     _f("risk.max_per_market_usd", 50),
            "max_daily_loss_usd":     _f("risk.max_daily_loss_usd", 50),
        }
        cfg.setdefault("strategies", {})
        cfg["strategies"]["market_making"] = {
            "enabled":     _b("mm.enabled"),
            "spread_pct":  _f("mm.spread_pct", 2) / 100,   # convert % to fraction
            "max_pos_usd": _f("mm.max_pos_usd", 50),
            "refresh_sec": int(_f("mm.refresh_sec", 10)),
            "max_markets": int(_f("mm.max_markets", 5)),
        }
        cfg["strategies"]["value_betting"] = {
            "enabled":        _b("vb.enabled"),
            "min_edge_pct":   _f("vb.min_edge_pct", 5) / 100,
            "max_pos_usd":    _f("vb.max_pos_usd", 30),
            "kelly_fraction": _f("vb.kelly_fraction", 0.25),
        }
        cfg["strategies"]["copy_trading"] = {
            "enabled":            _b("ct.enabled"),
            "scale_factor":       _f("ct.scale_factor", 0.10),
            "max_pos_usd":        _f("ct.max_pos_usd", 20),
            "check_interval_min": int(_f("ct.check_interval_min", 5)),
        }
        cfg["strategies"]["time_decay"] = {
            "enabled":              _b("td.enabled"),
            "hours_before_expiry":  _f("td.hours_before_expiry", 6),
            "min_no_price":         _f("td.min_no_price", 0.85),
        }

        # Persist to disk
        self.app.save_config()

        # Hot-reload running engine if connected
        from core.engine import BotEngine
        engine = BotEngine.instance()
        if engine.running:
            from core.risk_manager import RiskManager
            RiskManager.instance().update_config(cfg["risk"])
            for name in ("market_making", "value_betting", "copy_trading", "time_decay"):
                engine.reload_strategy_config(name, cfg["strategies"][name])

        from tkinter import messagebox
        messagebox.showinfo("Saved", "Strategy config saved and applied.")

    # ── Fair value override ───────────────────────────────────────────────────
    def _set_fair_value(self) -> None:
        token_id = self._fv_token_entry.get().strip()
        prob_str = self._fv_prob_entry.get().strip()
        if not token_id:
            return
        try:
            prob = float(prob_str)
        except ValueError:
            from tkinter import messagebox
            messagebox.showerror("Invalid", "Probability must be a number between 0 and 1.")
            return

        from core.engine import BotEngine
        engine = BotEngine.instance()
        vb_strat = engine.get_strategy("value_betting")
        if vb_strat:
            vb_strat.set_fair_value(token_id, prob)
            log.info("Fair value set: %s → %.4f", token_id[:12], prob)

    def _resume_risk(self) -> None:
        from core.risk_manager import RiskManager
        RiskManager.instance().resume()
        from tkinter import messagebox
        messagebox.showinfo("Risk", "Trading halt cleared – bot will resume on next tick.")
