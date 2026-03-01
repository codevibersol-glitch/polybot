# PolyBot – Polymarket Automated Desktop Trading Bot

A professional-grade, fully automated prediction-market trading bot with a
beautiful dark-mode GUI.  One-click connect → runs forever in background.

---

## Quick Start (5 minutes)

### 1. Install Python 3.10+
Download from [python.org](https://python.org).  Python 3.11 recommended.

### 2. Install dependencies
```bash
cd polybot
pip install -r requirements.txt
```

### 3. Export your Polymarket private key
1. Go to [polymarket.com](https://polymarket.com)
2. Log in → click your avatar → **Settings**
3. Click **Export Private Key**
4. **Copy the key** – it starts with `0x` and is 64 hex characters

> ⚠ **Security**: Your private key gives full access to your wallet.
> Never share it. PolyBot never logs it or sends it anywhere except the
> official Polymarket CLOB at `clob.polymarket.com`.

### 4. Get your wallet address
It's the `0x…` address shown in your Polymarket profile / Settings.

### 5. Fund your wallet
- You need **USDC on Polygon** to trade
- You need a small amount of **MATIC** for gas fees on allowance setup
- Bridge USDC from Ethereum to Polygon at [app.polygon.technology](https://app.polygon.technology)

### 6. Run PolyBot
```bash
python main.py
```

### 7. Set allowances (one time only)
1. Go to **Setup** tab
2. Enter your Private Key and Wallet Address
3. Click **Connect & Go**
4. Click **Check Allowances** – if either shows ✗, click **Set Allowances**
5. This sends two Polygon transactions (requires ~0.01 MATIC for gas)

### 8. Start trading
1. **Markets** tab → **Refresh** → find a market you want to trade
2. Click **Watch** (just monitor) or **Auto-Trade** (let bot trade it)
3. **Strategies** tab → enable the strategies you want → **Save All**
4. **Dashboard** tab → watch your positions update live

---

## Strategies

### Market Making
Posts two-sided limit orders (bid + ask) around the midpoint.
Earns the spread on each fill.  Best on high-liquidity markets.

- **Spread %** – base spread width (e.g. `2` = 2%)
- **Max Position** – USDC cap per market
- **Refresh** – how often to re-quote in seconds
- **Max Markets** – how many markets to MM simultaneously

### Value Betting
Bets when the implied probability diverges from your estimate of fair value
by more than the edge threshold.  Uses Kelly-fraction position sizing.

- **Min Edge %** – minimum edge to trade (e.g. `5` = 5% edge required)
- **Kelly Fraction** – conservative fraction of full Kelly (0.25 = 25%)
- **Fair-Value Override** – paste a token ID and your probability estimate
  to have the bot trade purely on your signal

### Copy Trading Lite
Mirrors recent trades of top-volume Polymarket traders at a scaled-down size.

- **Scale Factor** – 0.10 = trade 10% of what they trade
- **Check Interval** – how often to poll for new trades (minutes)

### Time Decay Auto-Sell
Near market expiry, "No" tokens on unresolved markets become more valuable.
This strategy:
1. Buys cheap "No" tokens on markets approaching expiry without resolution
2. Auto-sells "Yes" positions before resolution deadline if price hasn't moved

- **Hours Before Expiry** – trigger window (e.g. `6` = last 6 hours)
- **Min No Price** – minimum No price to lock in profit (e.g. `0.85`)

---

## Risk Management

All orders pass through the risk manager before placement:

| Rule | Default | Description |
|------|---------|-------------|
| Max Total Exposure | $200 | Sum of all open position cost bases |
| Max Per Market | $50 | Max USDC in any single market |
| Daily Loss Stop | $50 | Halts trading if daily P/L < −$50 |

When halted, go to **Strategies** tab → **Resume After Halt**.

---

## Security

| What | How |
|------|-----|
| Private key in memory | Only in Python process RAM, never in logs |
| Private key on disk | OS keychain (macOS Keychain / Windows Credential Store) or AES-256-GCM encrypted file |
| API keys | Derived deterministically from private key – no separate secret management |
| All CLOB calls | HTTPS to official `clob.polymarket.com` only |
| WS connections | WSS (TLS) to official Polymarket endpoints |

---

## File Structure

```
polybot/
├── main.py                  # Entry point: python main.py
├── requirements.txt
├── config.json              # Auto-created on first save (no private key)
├── config.json.example      # Template
├── polybot.log              # Rotating log file (5 MB × 3 backups)
│
├── core/
│   ├── client.py            # Thread-safe ClobClient wrapper with retries
│   ├── engine.py            # Central coordinator: connects, ticks, routes events
│   ├── market_data.py       # Gamma API integration + caching
│   ├── websocket_manager.py # Persistent WS connections with auto-reconnect
│   ├── order_manager.py     # Order lifecycle: place → track → fill → cancel
│   ├── position_manager.py  # Position book + P/L calculation + CSV export
│   └── risk_manager.py      # Hard-stop risk gates + Kelly sizing
│
├── strategies/
│   ├── base.py              # Abstract base class – extend to add strategies
│   ├── market_making.py     # Dynamic two-sided quoting
│   ├── value_betting.py     # Probability-edge / fair-value trading
│   ├── copy_trading.py      # Mirror top traders at scaled size
│   └── time_decay.py        # Pre-expiry liquidation + No-token accumulation
│
├── gui/
│   ├── app.py               # Main CTk window, tab layout, GUI update loop
│   ├── dashboard_tab.py     # Portfolio cards + positions + orders tables
│   ├── markets_tab.py       # Searchable market browser with Watch/Auto-Trade
│   ├── strategies_tab.py    # Strategy config + risk settings
│   ├── logs_tab.py          # Real-time log viewer + manual orders + emergency flat
│   └── setup_tab.py         # Credentials, allowance check, Connect & Go
│
└── utils/
    ├── logger.py             # Rotating file log + GUI queue log handler
    ├── config.py             # JSON config load/save with schema defaults
    └── crypto.py             # OS keychain + AES-GCM key encryption
```

---

## Adding a Custom Strategy

1. Create `strategies/my_strategy.py`:

```python
from strategies.base import BaseStrategy
from core.order_manager import OrderManager

class MyStrategy(BaseStrategy):
    name = "my_strategy"

    def on_tick(self, market_snapshot, price_map):
        for market in market_snapshot:
            yes_token = self.token_for_outcome(market, "Yes")
            price = price_map.get(yes_token, 0)
            if price < 0.40:   # buy anything under 40¢
                OrderManager.instance().place_order(
                    token_id=yes_token,
                    market_id=market["id"],
                    question=market["question"],
                    side="BUY",
                    price=price + 0.01,
                    size=5.0,
                    strategy=self.name,
                )
```

2. Register it in `core/engine.py` → `_load_strategies()`:
```python
from strategies.my_strategy import MyStrategy
self._strategies.append(MyStrategy(strat_cfg.get("my_strategy", {})))
```

---

## Build Single Executable

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name PolyBot \
    --add-data "config.json.example:." \
    main.py
# Output: dist/PolyBot (macOS/Linux) or dist/PolyBot.exe (Windows)
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| Connection failed | Check private key starts with `0x`, wallet address is correct |
| Allowance error | Make sure you have MATIC for gas; click Set Allowances |
| Orders not filling | Check USDC balance on Polygon, check risk limits in Strategies tab |
| Bot halted | Strategies tab → Resume After Halt button |
| WS disconnects | Auto-reconnects – check your internet connection |

Logs are saved to `polybot.log` in the app folder.  Set level to DEBUG for
maximum detail.
