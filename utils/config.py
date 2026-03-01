"""
utils/config.py
───────────────
Persistent configuration: load from / save to config.json.

Schema (all keys optional – defaults filled in automatically):
{
  "wallet_address"  : "0x...",
  "signature_type"  : 0,
  "remember_key"    : false,
  "strategies": {
    "market_making": {
      "enabled"      : false,
      "spread_pct"   : 0.02,
      "max_pos_usd"  : 50.0,
      "refresh_sec"  : 10,
      "max_markets"  : 5
    },
    "value_betting": {
      "enabled"      : false,
      "min_edge_pct" : 0.05,
      "max_pos_usd"  : 30.0,
      "kelly_fraction": 0.25
    },
    "copy_trading": {
      "enabled"      : false,
      "scale_factor" : 0.10,
      "max_pos_usd"  : 20.0,
      "check_interval_min": 5
    },
    "time_decay": {
      "enabled"      : false,
      "hours_before_expiry": 6,
      "min_no_price" : 0.85
    }
  },
  "risk": {
    "max_total_exposure_usd": 200.0,
    "max_daily_loss_usd"    : 50.0,
    "max_per_market_usd"    : 50.0
  },
  "watched_markets"  : [],
  "auto_trade_markets": []
}
"""

import json
import copy
from pathlib import Path
from utils.logger import get_logger

log = get_logger(__name__)

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

# ── Default configuration (always safe to run with) ───────────────────────────
_DEFAULTS: dict = {
    "wallet_address": "",
    "signature_type": 0,
    "remember_key": False,
    "strategies": {
        "market_making": {
            "enabled": False,
            "spread_pct": 0.02,
            "max_pos_usd": 50.0,
            "refresh_sec": 10,
            "max_markets": 5,
        },
        "value_betting": {
            "enabled": False,
            "min_edge_pct": 0.05,
            "max_pos_usd": 30.0,
            "kelly_fraction": 0.25,
        },
        "copy_trading": {
            "enabled": False,
            "scale_factor": 0.10,
            "max_pos_usd": 20.0,
            "check_interval_min": 5,
        },
        "time_decay": {
            "enabled": False,
            "hours_before_expiry": 6,
            "min_no_price": 0.85,
        },
    },
    "risk": {
        "max_total_exposure_usd": 200.0,
        "max_daily_loss_usd": 50.0,
        "max_per_market_usd": 50.0,
    },
    "watched_markets": [],
    "auto_trade_markets": [],
}


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge *override* into a deep copy of *base*.
    Keys in base that are missing from override are preserved.
    """
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load() -> dict:
    """Load config from disk, merging with defaults for any missing keys."""
    if not CONFIG_FILE.exists():
        log.info("No config.json found – using defaults.")
        return copy.deepcopy(_DEFAULTS)
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            on_disk = json.load(f)
        merged = _deep_merge(_DEFAULTS, on_disk)
        log.debug("Config loaded from %s", CONFIG_FILE)
        return merged
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to load config: %s – using defaults.", exc)
        return copy.deepcopy(_DEFAULTS)


def save(cfg: dict) -> None:
    """Persist config to disk.  Private key is NEVER saved here."""
    # Safety: strip private key if caller accidentally included it
    safe = copy.deepcopy(cfg)
    safe.pop("private_key", None)
    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(safe, f, indent=2)
        log.debug("Config saved to %s", CONFIG_FILE)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to save config: %s", exc)


def get_nested(cfg: dict, *keys, default=None):
    """Safe nested key access: get_nested(cfg, 'risk', 'max_per_market_usd')."""
    node = cfg
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node
