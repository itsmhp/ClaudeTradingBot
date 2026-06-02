# Rules.json Documentation

## ClaudeTradingBot Configuration Reference

This document explains every field in `rules.json` — the configuration file Claude AI reads before every chart analysis session.

---

## File Location

```
ClaudeTradingBot/
├── rules.json              ← THIS FILE (project root)
└── strategies/rules.json   ← Symlink or copy (alternative path)
```

The bot loads `rules.json` from the project root. Claude reads this file in its system prompt context to understand what instruments to analyze, what criteria to check, and what thresholds to enforce.

---

## Section Reference

### `_meta`

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Human-readable config name |
| `version` | string | Semantic version of this config |
| `broker` | string | Broker this is configured for (Exness) |
| `updated` | string | Last modification date |
| `description` | string | What this file does |

---

### `watchlist`

Categorized list of all instruments the bot monitors. Instruments must be available on your Exness MT5 account.

| Category | Instruments | Notes |
|----------|-------------|-------|
| `commodities` | XAUUSD | Gold |
| `crypto` | BTCUSD | Bitcoin (24/7 market) |
| `forex_majors` | EURUSD, GBPUSD, USDJPY | Major forex pairs |
| `indices` | NAS100, US30 | US stock indices |

**To add a new instrument:** Add it to the appropriate category, then create a full entry in `instruments`.

---

### `instruments.<SYMBOL>`

Per-instrument configuration. Every tradeable symbol must have a complete entry.

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `display_name` | string | Human-readable name | "Gold vs USD" |
| `category` | string | Category from watchlist | "commodities" |
| `preferred_modes` | array | Which strategies to use | ["scalp", "swing"] |
| `preferred_sessions` | array | Best sessions to trade | ["london", "new_york"] |
| `spread_cap_points` | int | Max spread to allow entry (in points) | 35 |
| `typical_spread_points` | int | Normal spread in good conditions | 25 |
| `min_move_pips` | float | Minimum pip move to consider as signal | 3.0 |
| `pip_size` | float | Value of 1 pip for this instrument | 0.01 |
| `digits` | int | Decimal places in price | 2 |
| `contract_size` | int | Standard lot contract size | 100 |
| `min_lot` | float | Minimum lot size | 0.01 |
| `max_lot` | float | Maximum lot size | 50.0 |
| `lot_step` | float | Lot increment | 0.01 |
| `magic_number` | int | Unique MT5 magic number for this instrument | 234001 |
| `avoid_minutes_before_news` | int | Minutes before news to stop trading | 30 |
| `avoid_minutes_after_news` | int | Minutes after news to wait | 30 |

#### `scalp_settings`

| Field | Type | Description |
|-------|------|-------------|
| `timeframes` | array | Timeframes for scalp analysis |
| `tp_pips` | float | Take profit distance in pips |
| `sl_pips` | float | Stop loss distance in pips |
| `max_open_positions` | int | Max concurrent scalp positions for this pair |
| `min_session_overlap` | bool | Require London/NY overlap for entry |
| `trailing_after_pips` | float | Activate trailing stop after this many pips profit |

#### `swing_settings`

| Field | Type | Description |
|-------|------|-------------|
| `timeframes` | array | Timeframes for swing analysis |
| `tp1_rr` | float | First take profit risk-reward ratio |
| `tp2_rr` | float | Second take profit risk-reward ratio |
| `max_open_positions` | int | Max concurrent swing positions for this pair |
| `use_atr_for_sl` | bool | Use ATR-based stop loss |
| `atr_multiplier_sl` | float | ATR multiplier for stop loss distance |
| `move_sl_to_be_after_tp1` | bool | Move stop to breakeven after TP1 hit |

---

### `global_rules`

System-wide risk management and execution rules.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_concurrent_trades` | int | 3 | Maximum total open positions across ALL instruments |
| `max_daily_loss_pct` | float | 3.0 | Daily loss limit as % of equity. Bot auto-pauses if breached |
| `default_risk_per_trade_pct` | float | 1.0 | Risk per trade as % of account equity |
| `default_min_rr` | float | 2.0 | Minimum Risk:Reward ratio to accept a signal |
| `min_confidence_to_execute` | int | 65 | Min confidence score to auto-execute in AUTO_EXECUTE mode |
| `min_confidence_to_alert` | int | 55 | Min confidence to send a Telegram alert |
| `only_pending_orders` | bool | true | NEVER use market orders |
| `allowed_order_types` | array | [...] | Only these MT5 order types are permitted |
| `never_use_market_orders` | bool | true | Redundant safety flag |
| `move_sl_to_breakeven_after_tp1` | bool | true | After TP1 hit, SL moves to entry price |
| `partial_close_at_tp1_pct` | int | 50 | Close 50% of position at TP1 |
| `max_spread_multiplier_vs_typical` | float | 2.0 | If current spread > 2× typical, reject signal |
| `max_slippage_points` | int | 20 | Maximum acceptable slippage for pending order fill |
| `order_expiry` | string | "GTC" | Good Till Cancelled (orders persist until filled or cancelled) |

---

### `sessions`

Trading session definitions with UTC times.

| Session | UTC Hours | Best For |
|---------|-----------|----------|
| `asian` | 00:00 – 08:00 | USDJPY range trading |
| `london` | 07:00 – 16:00 | Forex breakouts, Gold |
| `new_york` | 13:00 – 21:00 | All instruments, indices |
| `london_ny_overlap` | 13:00 – 16:00 | Scalping (tightest spreads) |
| `dead_zone` | 21:00 – 00:00 | **NO SCALPING** — avoid |

**Important:** Sessions overlap intentionally. The bot checks if current UTC time falls within ANY of the instrument's `preferred_sessions`.

---

### `news_blackout_events`

High-impact economic events that cause extreme volatility. The bot will NOT generate signals within the blackout window.

| Field | Description |
|-------|-------------|
| `event` | Event name |
| `impact` | "high" or "extreme" |
| `frequency` | How often it occurs |
| `avoid_minutes_before` | Minutes before event to stop trading |
| `avoid_minutes_after` | Minutes after event to resume |
| `affected_pairs` | Which instruments are affected |

**How it works:** The bot integrates with an economic calendar (or manual schedule). If the current time is within `avoid_minutes_before` of a scheduled event, and the target instrument is in `affected_pairs`, the signal is rejected with reason "NEWS_BLACKOUT".

---

### `strategy_definitions`

Detailed entry/exit criteria for each strategy type.

#### Scalping Entry Criteria

1. **EMA Crossover** — EMA 9 crosses EMA 21 in trade direction
2. **RSI Filter** — RSI(14) in acceptable range (not overbought/oversold against trade)
3. **S/R Proximity** — Price near a support/resistance level
4. **Trend Alignment** — Higher timeframe EMA 50 confirms direction
5. **Spread Check** — Current spread below instrument's cap
6. **Session Check** — Currently in a valid trading session

All criteria must be met. If any fails, the signal is not generated.

#### Swing Entry Criteria

1. **Structure Break** — Higher high (buy) or lower low (sell) on H4/D1
2. **EMA Position** — Price above EMA50 above EMA200 (buy) or inverse (sell)
3. **RSI Confirmation** — Momentum direction confirmed without extremes
4. **Pullback Entry** — Wait for price to retrace to EMA50 or Fibonacci level
5. **Volume Confirmation** — Breakout candle exceeds 20-period volume average

---

### `indicators`

Technical indicators that must be loaded on TradingView charts.

**Required indicators** must be visible on the chart for Claude to analyze:
- RSI (14)
- EMA (9, 21, 50, 200)
- ATR (14)
- MACD (12, 26, 9)
- Volume (20-period MA)

**Optional indicators** provide additional confluence:
- Fibonacci Retracement
- Bollinger Bands (20, 2)
- VWAP

---

### `bias_criteria`

How Claude determines market bias before looking for setups.

| Bias | Key Conditions | Action |
|------|----------------|--------|
| **Bullish** | Price > EMA50 > EMA200, RSI 45-70, HH/HL structure | Look for BUY setups |
| **Bearish** | Price < EMA50 < EMA200, RSI 30-55, LH/LL structure | Look for SELL setups |
| **Neutral** | No clear direction, RSI 40-60, range-bound | NO_TRADE |

---

### `claude_instructions`

Directives embedded in Claude's system prompt for analysis.

#### Analysis Steps (in order)
1. Check D1 structure → determine bias
2. Validate strategy criteria on entry timeframe
3. Calculate precise levels (entry, SL, TP1, TP2)
4. Run mandatory checks (spread, R:R, news, session, position limit)
5. Assign confidence score
6. Output structured JSON signal

#### Confidence Scoring

| Score | Meaning | Action |
|-------|---------|--------|
| 90-100 | Perfect alignment | Execute immediately |
| 75-89 | Strong setup | Execute with standard size |
| 65-74 | Valid but not ideal | Execute with reduced confidence note |
| 55-64 | Weak setup | Alert only, no execution |
| <55 | No valid setup | Return NO_TRADE |

---

## How to Customize

### Adding a New Instrument

1. Add the symbol to the appropriate `watchlist` category
2. Create a full entry in `instruments` with all fields
3. Assign a unique `magic_number` (increment from 234007)
4. Define both `scalp_settings` and `swing_settings`
5. Test with `SIGNAL_ONLY` mode first

### Adjusting Risk

- Change `default_risk_per_trade_pct` (1% is conservative, 2% is moderate)
- Adjust `max_daily_loss_pct` (3% is standard, 5% is aggressive)
- Modify `default_min_rr` (2.0 minimum recommended)

### Changing Spread Caps

Spread caps should reflect your actual Exness account type:
- **Standard Account**: Use values in this config
- **Raw Spread Account**: Reduce all caps by ~60%
- **Zero Account**: Set forex caps to 5-8 points

### Modifying Sessions

If you're in a different timezone or prefer specific hours:
- Edit `start_utc` and `end_utc` in the sessions section
- Update instrument `preferred_sessions` arrays accordingly

---

## Validation

The bot validates `rules.json` on startup. It checks:
- All instruments in watchlist have a full `instruments` entry
- All `magic_number` values are unique
- `spread_cap_points` > `typical_spread_points` for all instruments
- R:R ratios are >= 1.5
- Session times are valid UTC format
- News events have valid `affected_pairs` that exist in watchlist

If validation fails, the bot exits with a descriptive error message.
