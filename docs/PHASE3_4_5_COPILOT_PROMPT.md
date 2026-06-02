# Phase 3, 4, 5 — GitHub Copilot Prompts
> Paste setiap prompt secara berurutan ke Copilot Chat

---

# ═══════════════════════════════════════
# PHASE 3: BACKTESTING
# ═══════════════════════════════════════

## PROMPT 3.1 of 3 — Historical Data Loader + vectorbt Integration

```
Read MASTER_CONTEXT.md sections 7 (MT5 Integration), 6 (Trading Strategy Logic),
and the backtesting snippet in section 18 before writing any code.

Create two new files: `backtesting/data_loader.py` and `backtesting/engine.py`.
Also add `vectorbt`, `pandas`, `numpy` to requirements.txt if not already present.

─── FILE 1: backtesting/data_loader.py ───

Class `HistoricalDataLoader`:
- `__init__` — connect to MT5Bridge, define timeframe map:
  {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
   "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}

- `load(symbol: str, timeframe: str, count: int = 5000) -> pd.DataFrame`
  — call mt5.copy_rates_from_pos(symbol, timeframe_constant, 0, count)
  — convert to DataFrame with columns: time, open, high, low, close, tick_volume, spread
  — parse time column as UTC datetime, set as index
  — return the DataFrame

- `load_range(symbol: str, timeframe: str, date_from: datetime, date_to: datetime) -> pd.DataFrame`
  — use mt5.copy_rates_range() for specific date range
  — same format as load()

- `load_all_pairs(timeframe: str, count: int = 5000) -> dict[str, pd.DataFrame]`
  — load data for all pairs from strategies/rules.json watchlist
  — return dict keyed by symbol

- `save_to_csv(df: pd.DataFrame, symbol: str, timeframe: str) -> Path`
  — save to backtesting/data/{symbol}_{timeframe}.csv
  — return the file path

- `load_from_csv(symbol: str, timeframe: str) -> pd.DataFrame | None`
  — load cached CSV if it exists (check file age: if older than 24h, return None so caller re-fetches)
  — return None if file doesn't exist

─── FILE 2: backtesting/engine.py ───

Class `BacktestEngine`:
- `__init__(data_loader: HistoricalDataLoader)` — store loader, load rules.json

- `run_swing_backtest(symbol: str, timeframe: str = "H4", count: int = 5000, init_cash: float = 10000.0) -> dict`
  — Load data via data_loader (try CSV cache first, fallback to MT5)
  — Compute indicators on the DataFrame using pandas:
    * EMA 50: close.ewm(span=50).mean()
    * EMA 200: close.ewm(span=200).mean()
    * RSI 14: implement RSI using rolling mean of gains/losses
  — Generate entry signals (boolean Series):
    * BUY entry: close > ema50, ema50 > ema200, rsi between 45-70, close crossed above ema50 in last 3 bars
    * SELL entry: close < ema50, ema50 < ema200, rsi between 30-55, close crossed below ema50 in last 3 bars
  — Generate exit signals:
    * BUY exit: close crosses below ema50 OR rsi > 75
    * SELL exit: close crosses above ema50 OR rsi < 25
  — Use vectorbt: `vbt.Portfolio.from_signals(close, entries=buy_entries, exits=buy_exits, init_cash=init_cash, fees=0.0001)`
  — Extract and return stats dict:
    {
      "symbol": symbol, "timeframe": timeframe, "period_days": N,
      "total_trades": int, "win_rate": float, "net_pnl": float,
      "profit_factor": float, "max_drawdown_pct": float,
      "sharpe_ratio": float, "avg_trade_duration_hours": float,
      "best_trade_pct": float, "worst_trade_pct": float,
      "total_return_pct": float
    }

- `run_scalping_backtest(symbol: str, timeframe: str = "M5", count: int = 10000, init_cash: float = 10000.0) -> dict`
  — Same structure but scalping indicators:
    * EMA 9, EMA 21 (faster EMAs)
    * RSI 14
  — BUY entry: ema9 crosses above ema21, rsi between 40-65
  — SELL entry: ema9 crosses below ema21, rsi between 35-60
  — EMA crossover detection: (ema9 > ema21) & (ema9.shift(1) <= ema21.shift(1))
  — Return same stats dict format

- `compare_strategies(symbol: str, init_cash: float = 10000.0) -> dict`
  — Run both swing (H4) and scalping (M5) for same symbol
  — Return side-by-side comparison dict with both results

- `run_all_pairs(strategy: str = "swing", init_cash: float = 10000.0) -> list[dict]`
  — Run backtest for every pair in rules.json watchlist
  — Return list of stats dicts sorted by net_pnl descending

Rules:
- Never hardcode price data — always load from MT5 or CSV cache
- Handle MT5 returning None (connection issue) gracefully — raise DataLoadError
- All DataFrames must have timezone-aware UTC index
- vectorbt Portfolio must use fees=0.0001 (0.01% per trade, approximates spread cost)
```

---

## PROMPT 3.2 of 3 — Parameter Optimization + Walk-Forward + Monte Carlo

```
Read MASTER_CONTEXT.md section 18 (Testing Plan) and `backtesting/engine.py` from the
previous prompt before writing any code.

Create `backtesting/optimizer.py` and `backtesting/monte_carlo.py`.
Add `scipy`, `itertools` usage (stdlib) — no new pip installs needed beyond vectorbt/pandas.

─── FILE 1: backtesting/optimizer.py ───

Class `StrategyOptimizer`:
- `__init__(engine: BacktestEngine)` — store engine

- `optimize_swing_params(symbol: str, timeframe: str = "H4") -> dict`
  — Grid search over parameter combinations:
    * ema_fast: [20, 50, 100]
    * ema_slow: [100, 200]
    * rsi_low: [40, 45, 50]
    * rsi_high: [60, 65, 70]
  — For each combination: run a modified backtest using those params (add params argument
    to engine.run_swing_backtest), collect stats
  — Filter out combinations with < 10 total trades (not statistically meaningful)
  — Rank by Sharpe ratio descending, then profit_factor descending
  — Return: {"best_params": dict, "best_stats": dict, "all_results": list[dict] (top 10)}

- `optimize_scalping_params(symbol: str, timeframe: str = "M5") -> dict`
  — Grid search:
    * ema_fast: [5, 9, 13]
    * ema_slow: [15, 21, 34]
    * rsi_low: [35, 40, 45]
    * rsi_high: [55, 60, 65]
  — Same structure as swing optimizer
  — Return same format

- `walk_forward_analysis(symbol: str, strategy: str = "swing", n_splits: int = 5) -> dict`
  — Split historical data into n_splits folds (in-sample + out-of-sample)
  — For each fold:
    1. In-sample: run optimize to find best params
    2. Out-of-sample: run backtest with those best params on unseen data
    3. Record in-sample stats vs out-of-sample stats
  — Return:
    {
      "symbol": symbol, "strategy": strategy, "n_splits": n_splits,
      "folds": [{"fold": i, "in_sample": stats, "out_of_sample": stats, "best_params": params}],
      "avg_out_of_sample_win_rate": float,
      "avg_out_of_sample_return_pct": float,
      "consistency_score": float  # out-of-sample return / in-sample return (closer to 1.0 = better)
    }

- `compare_all_pairs_performance(strategy: str = "swing") -> pd.DataFrame`
  — Run backtest for ALL pairs, collect stats
  — Return as a pandas DataFrame sorted by Sharpe ratio
  — Columns: symbol, total_trades, win_rate, net_pnl, profit_factor, max_drawdown_pct, sharpe_ratio

─── FILE 2: backtesting/monte_carlo.py ───

Class `MonteCarloSimulator`:
- `__init__(n_simulations: int = 1000)` — store n_simulations

- `simulate(trade_returns: list[float], init_cash: float = 10000.0) -> dict`
  — `trade_returns` is a list of trade P&L values (positive or negative floats)
  — Run n_simulations iterations:
    * Each iteration: shuffle trade_returns randomly (random.shuffle on a copy)
    * Compute equity curve: cumsum starting from init_cash
    * Record final equity, max drawdown, and whether daily loss limit was hit (drawdown > 3%)
  — Return:
    {
      "n_simulations": int,
      "init_cash": float,
      "median_final_equity": float,
      "p5_final_equity": float,    # 5th percentile (worst 5% of scenarios)
      "p95_final_equity": float,   # 95th percentile (best 5% of scenarios)
      "probability_of_loss": float,  # % simulations ending below init_cash
      "probability_of_ruin": float,  # % simulations hitting 3% daily loss at some point
      "median_max_drawdown_pct": float,
      "worst_case_drawdown_pct": float,  # p95 drawdown
      "expected_return_pct": float  # median
    }

- `simulate_from_backtest(backtest_stats: dict, init_cash: float = 10000.0) -> dict`
  — Extract trade returns from backtest_stats
  — Call simulate() and return results

- `risk_of_ruin(win_rate: float, avg_win: float, avg_loss: float, max_loss_pct: float = 3.0, n_trades: int = 100) -> float`
  — Analytical formula (Kelly-based): probability account drops by max_loss_pct
    given win_rate, avg_win/avg_loss ratio over n_trades
  — Return as float between 0.0 and 1.0
```

---

## PROMPT 3.3 of 3 — Backtesting API Endpoints + Dashboard Tab

```
Read MASTER_CONTEXT.md section 13 (API Endpoints), `api/routes.py`, and
`dashboard/index.html` before writing any code.

─── FILE 1: api/routes.py — add backtesting endpoints ───

Add a new router prefix `/backtest`:

- GET `/backtest/run?symbol=XAUUSD&strategy=swing&timeframe=H4&count=5000`
  — Runs BacktestEngine.run_swing_backtest() or run_scalping_backtest() based on strategy param
  — Returns the stats dict
  — Long-running: respond immediately with {"job_id": uuid, "status": "running"}
    then run in background via asyncio.create_task(), store result in an in-memory dict
  — Use BackgroundTasks from FastAPI

- GET `/backtest/result/{job_id}`
  — Returns {"status": "running"} or {"status": "done", "result": stats_dict}

- GET `/backtest/compare?symbol=XAUUSD`
  — Calls BacktestEngine.compare_strategies(), returns both swing and scalping stats side by side

- GET `/backtest/all-pairs?strategy=swing`
  — Calls BacktestEngine.run_all_pairs(), returns list of stats sorted by net_pnl

- POST `/backtest/optimize`
  — Body: {"symbol": "XAUUSD", "strategy": "swing", "timeframe": "H4"}
  — Runs StrategyOptimizer, returns best_params + top 10 results
  — Also background task with job_id pattern

- POST `/backtest/monte-carlo`
  — Body: {"symbol": "XAUUSD", "strategy": "swing", "n_simulations": 1000}
  — Runs full backtest then Monte Carlo simulation
  — Returns MonteCarloSimulator results dict

─── FILE 2: dashboard/index.html — add Backtesting tab ───

Add a 6th section to the sidebar: "Backtesting"

Backtesting section layout:
- Top row: two dropdowns (Symbol, Strategy) + Run Backtest button
- Progress indicator while job is running (poll GET /backtest/result/{job_id} every 2s)
- Results panel (hidden until job completes):
  * 6 stat cards: Total Trades | Win Rate | Net P&L | Profit Factor | Max Drawdown | Sharpe Ratio
  * Equity curve chart (Chart.js line) generated from cumulative trade returns
  * Trade distribution histogram (Chart.js bar) — count of trades by return bucket
- "Compare All Pairs" button → calls GET /backtest/all-pairs, renders comparison table:
  Pair | Strategy | Trades | Win% | Net P&L | Profit Factor | Max DD | Sharpe
  Sorted by Sharpe, color-code Sharpe: green > 1.0, amber 0.5-1.0, red < 0.5
- "Run Monte Carlo" button (shows after backtest completes):
  * Shows probability of loss, probability of ruin, P5/P95 equity range
  * Renders a fan chart (multiple equity curve scenarios overlaid, semi-transparent)

Update MASTER_CONTEXT.md Section 19 to mark all Phase 3 items as complete.
```

---

# ═══════════════════════════════════════
# PHASE 4: MULTI-ACCOUNT / COPY TRADING
# ═══════════════════════════════════════

## PROMPT 4.1 of 3 — Multi-Account Architecture + Account Manager

```
Read MASTER_CONTEXT.md sections 7 (MT5 Integration), 10 (Risk Management), and
`core/mt5_bridge.py` from Phase 1 before writing any code.

Create `multi_account/account_manager.py` and `multi_account/account_registry.py`.
Add new table `mt5_accounts` to the database schema.

─── FILE 1: multi_account/account_registry.py ───

Pydantic model `MT5Account`:
- account_id: str (unique slug, e.g. "exness_main", "exness_micro")
- label: str (human-readable name)
- login: int
- password: str (stored encrypted — use Fernet from cryptography library)
- server: str (e.g. "Exness-MT5Real")
- broker: str (e.g. "Exness")
- is_master: bool (True = source account, False = follower)
- is_active: bool
- risk_per_trade_pct: float (can differ from global default)
- lot_size_multiplier: float (e.g. 0.5 = trade half the master lot size)
- copy_delay_seconds: int (delay before mirroring master trade, default 0)
- max_positions: int
- magic_number_offset: int (added to base magic numbers to distinguish accounts)
- created_at: datetime

SQLAlchemy ORM model `MT5AccountRecord` for persistence.

Class `AccountRegistry`:
- `__init__(db_session, encryption_key: bytes)` — load Fernet key from env ACCOUNT_ENCRYPTION_KEY
- `add_account(account: MT5Account) -> str` — encrypt password before saving, return account_id
- `get_account(account_id: str) -> MT5Account` — decrypt password on load
- `list_accounts() -> list[MT5Account]` — return all accounts (passwords masked)
- `get_master() -> MT5Account | None` — return account where is_master=True
- `get_followers() -> list[MT5Account]` — return accounts where is_master=False and is_active=True
- `deactivate_account(account_id: str) -> None`

Add to .env.example: ACCOUNT_ENCRYPTION_KEY (generate with Fernet.generate_key())

─── FILE 2: multi_account/account_manager.py ───

Class `AccountManager`:
- `__init__(registry: AccountRegistry)` — store registry
- `connections: dict[str, MT5Bridge]` — active MT5Bridge per account_id

- `connect_all() -> dict[str, bool]`
  — For each active account in registry, instantiate MT5Bridge with that account's credentials
  — Store in self.connections
  — Return dict of {account_id: connection_success}

- `connect_account(account_id: str) -> bool`
  — Connect a single account, add to self.connections

- `disconnect_account(account_id: str) -> None`
  — Clean disconnect, remove from self.connections

- `get_bridge(account_id: str) -> MT5Bridge`
  — Return the MT5Bridge for this account, raise if not connected

- `get_all_account_info() -> dict[str, dict]`
  — For each connected account: call bridge.get_account_info()
  — Return dict of {account_id: account_info_dict}

- `get_aggregated_status() -> dict`
  — Total equity across all accounts, total open positions, total daily P&L
  — Return: {"total_equity": float, "total_positions": int, "total_daily_pnl": float,
             "accounts": list[{account_id, label, equity, positions, daily_pnl, is_connected}]}
```

---

## PROMPT 4.2 of 3 — Copy Trading Engine

```
Read `multi_account/account_manager.py`, `core/signal_engine.py`, and
`core/risk_manager.py` from previous phases before writing any code.

Create `multi_account/copy_engine.py`.

─── FILE: multi_account/copy_engine.py ───

Class `CopyEngine`:
- `__init__(account_manager: AccountManager, registry: AccountRegistry)`

- `copy_signal_to_followers(signal: TradeSignal, master_lot_size: float, master_order_id: int) -> list[dict]`
  — Get all active follower accounts from registry
  — For each follower:
    1. Get follower's MT5Bridge from account_manager
    2. Get follower's account equity
    3. Calculate follower lot size:
       * proportional_lot = master_lot_size × follower.lot_size_multiplier
       * Alternatively if follower.risk_per_trade_pct is set: recalculate from scratch
         using follower's own equity and risk% (same formula as RiskManager)
       * Round to follower instrument's lot_step, clamp to min/max
    4. Apply copy_delay_seconds: await asyncio.sleep(follower.copy_delay_seconds)
    5. Build identical order request (same pair, direction, order_type, entry, SL, TP)
       with follower's magic_number (base + magic_number_offset)
       and comment: f"CTB_COPY_{master_order_id}"
    6. Call follower_bridge.place_pending_order(signal, follower_lot_size)
    7. Log result to database table `copy_trades`
  — Return list of results: [{account_id, success, order_id, lot_size, error}]

- `copy_cancel_to_followers(master_ticket: int) -> list[dict]`
  — Find copy trades linked to master_ticket in `copy_trades` table
  — For each follower: call bridge.cancel_order(follower_ticket)
  — Return results

- `get_copy_performance() -> dict`
  — Compare master vs each follower: net_pnl, win_rate, trade count
  — Return comparison dict

Add new SQLAlchemy model `CopyTradeRecord` for the `copy_trades` table:
- id, master_signal_id, master_order_id, follower_account_id, follower_order_id,
  symbol, direction, lot_size, status, profit, created_at, updated_at

Update `core/signal_engine.py`:
- In `process_pair()`, after a successful master order execution:
  if CopyEngine is initialized (injected optionally): call copy_signal_to_followers()
  Log copy results to database and send Telegram summary:
  "📋 Copy Trading: Signal copied to {N} accounts — {success_count} success / {fail_count} failed"
```

---

## PROMPT 4.3 of 3 — Multi-Account API + Dashboard Tab

```
Read `api/routes.py`, `multi_account/account_manager.py`, `multi_account/copy_engine.py`,
and `dashboard/index.html` before writing any code.

─── FILE 1: api/routes.py — add multi-account endpoints ───

New router prefix `/accounts`:

- GET `/accounts` — list all accounts (passwords masked)
- POST `/accounts` — add new account (body: MT5Account fields, password plaintext → encrypted server-side)
- DELETE `/accounts/{account_id}` — deactivate account (soft delete, is_active=False)
- GET `/accounts/{account_id}/status` — single account equity, positions, daily P&L
- GET `/accounts/aggregated` — calls AccountManager.get_aggregated_status()
- POST `/accounts/{account_id}/connect` — connect a specific account
- POST `/accounts/{account_id}/disconnect` — disconnect a specific account
- GET `/accounts/copy-performance` — calls CopyEngine.get_copy_performance()

Security note: POST /accounts stores sensitive credentials. Add a simple token check:
require header X-Bot-Token matching BOT_API_TOKEN from env (add to .env.example).
Apply this token check ONLY to /accounts POST and DELETE — other GET endpoints are fine without.

─── FILE 2: dashboard/index.html — add Multi-Account tab ───

Add a 7th section: "Accounts"

Layout:
- Top: "Aggregated Overview" card row — Total Equity | Total Positions | Total Daily P&L
  across all connected accounts
- Accounts table: one row per account showing:
  Label | Broker | Type (Master/Follower) | Status (connected/disconnected) |
  Equity | Open Positions | Daily P&L | Lot Multiplier | Actions
  Actions: Connect / Disconnect buttons (call POST /accounts/{id}/connect or disconnect)
- "Add Account" button → modal form with fields:
  Label, Login, Password, Server, Broker, Master/Follower toggle,
  Risk per trade %, Lot multiplier, Copy delay (seconds)
  On submit: POST /accounts with X-Bot-Token header (prompt user for token once, store in sessionStorage)
- Copy Trading Performance section (shown only if ≥ 1 follower exists):
  Table: Account | Trades | Win Rate | Net P&L | vs Master P&L diff
- Per-account equity chart: Chart.js line showing equity curve for each account
  (different color per account, legend with account labels)

Update MASTER_CONTEXT.md Section 19 to mark all Phase 4 items as complete.
```

---

# ═══════════════════════════════════════
# PHASE 5: ADVANCED AI FEATURES
# ═══════════════════════════════════════

## PROMPT 5.1 of 4 — Multi-Model Consensus (Claude + GPT)

```
Read MASTER_CONTEXT.md sections 8 (Claude AI Integration) and 9 (Signal Schema),
and `core/claude_client.py` from Phase 1 before writing any code.

Create `core/consensus_engine.py`. Add `openai>=1.0.0` to requirements.txt.
Add OPENAI_API_KEY and CONSENSUS_MODE (CLAUDE_ONLY / GPT_ONLY / CONSENSUS) to .env.example.

─── FILE: core/consensus_engine.py ───

Class `ConsensusEngine`:
- `__init__` — instantiate both AnthropicClient (from core/claude_client.py) and
  OpenAI client (openai.AsyncOpenAI(api_key=OPENAI_API_KEY))
  Load CONSENSUS_MODE from env (default: CLAUDE_ONLY)

- `analyze_chart_claude(pair, timeframe, chart_data) -> TradeSignal | NoTradeSignal`
  — Delegate to existing ClaudeClient.analyze_chart() — no changes there

- `analyze_chart_gpt(pair: str, timeframe: str, chart_data: dict) -> TradeSignal | NoTradeSignal`
  — Call OpenAI chat completions with gpt-4o-mini model (cost-efficient)
  — Use IDENTICAL system prompt and user prompt format as Claude client
    (same JSON schema, same rules.json context, same chart_data injection)
  — Parse JSON response the same way: strip markdown fences, validate with TradeSignal
  — If OpenAI returns a different direction than the prompt format specifies, log and return NoTradeSignal

- `analyze_with_consensus(pair: str, timeframe: str, chart_data: dict) -> dict`
  — Run BOTH models concurrently: await asyncio.gather(claude_task, gpt_task)
  — Compare results:
    * If both return NoTradeSignal → return {"consensus": "NO_TRADE", "agreement": True}
    * If both return TradeSignal with SAME direction:
      → "consensus_signal": use Claude's signal (Claude is primary)
      → "agreement": True
      → "combined_confidence": (claude.confidence + gpt.confidence) / 2
      → "confidence_boost": +5 (reward for agreement, capped at 95)
    * If signals DISAGREE (different direction):
      → "consensus": "DISAGREEMENT"
      → "agreement": False
      → Do NOT execute — treat as NoTradeSignal
      → Log disagreement with both reasonings for analysis
    * If one errors and the other succeeds → use the successful one, mark "single_model": True
  — Return full dict with claude_signal, gpt_signal, consensus result

- `get_consensus_signal(pair, timeframe, chart_data) -> TradeSignal | NoTradeSignal`
  — Based on CONSENSUS_MODE env var:
    * "CLAUDE_ONLY" → return analyze_chart_claude()
    * "GPT_ONLY" → return analyze_chart_gpt()
    * "CONSENSUS" → run analyze_with_consensus(), return consensus_signal or NoTradeSignal

Update `core/signal_engine.py`:
- Replace ClaudeClient call in process_pair() with ConsensusEngine.get_consensus_signal()
- Log which model(s) contributed to the signal
- Add "model_consensus" field to the Telegram alert message when CONSENSUS mode is active:
  "🤝 Consensus: Claude ✅ + GPT-4o ✅ (Agreement)" or "⚠️ Single model: Claude only"
```

---

## PROMPT 5.2 of 4 — Learning from Trade Outcomes + Dynamic Strategy Selection

```
Read MASTER_CONTEXT.md sections 12 (Database Schema), 9 (Signal Schema), and
`database/queries.py` from Phase 1 before writing any code.

Create `core/feedback_loop.py` and `core/market_regime.py`.

─── FILE 1: core/feedback_loop.py ───

This module updates Claude's system prompt dynamically based on past trade outcomes.

Class `FeedbackLoop`:
- `__init__(db_session_factory)` — store factory

- `get_recent_performance_by_pair(days: int = 30) -> dict[str, dict]`
  — Query executed_trades for last N days
  — For each symbol: compute win_rate, avg_rr_achieved, total_trades, net_pnl
  — Return dict keyed by symbol

- `get_performance_by_setup(days: int = 30) -> dict`
  — Group by (symbol, strategy, timeframe, order_type)
  — Return which specific setups are performing best/worst

- `build_performance_context(days: int = 30) -> str`
  — Generate a plain-English performance summary string for injection into Claude's prompt
  — Format:
    "Recent performance context (last {days} days):
     - XAUUSD SWING H4: 8 trades, 62% win rate, avg R:R achieved 1.8 — PERFORMING BELOW TARGET
     - EURUSD SCALPING M5: 15 trades, 73% win rate, avg R:R achieved 2.1 — PERFORMING WELL
     - Avoid: BTCUSD signals (only 3 trades, 33% win rate)
     - Prioritize: GBPUSD (5 trades, 80% win rate)"
  — This string will be appended to Claude's system prompt

- `should_reduce_size_for_pair(symbol: str) -> tuple[bool, float]`
  — If win_rate < 40% or net_pnl < 0 for a pair in last 14 days → suggest reducing lot size
  — Return (should_reduce: bool, suggested_multiplier: float)
    e.g. (True, 0.5) means trade half the normal lot size

Update `core/claude_client.py`:
- In analyze_chart(), call FeedbackLoop.build_performance_context() and append to system prompt
- The context string is appended after the main rules, so Claude is aware of what's working

─── FILE 2: core/market_regime.py ───

Class `MarketRegimeDetector`:
- `__init__(mt5_bridge: MT5Bridge)`

- `detect_regime(symbol: str, timeframe: str = "D1") -> dict`
  — Load last 100 candles for the symbol
  — Compute:
    * Trend direction: EMA50 vs EMA200 (bullish/bearish/transitioning)
    * Volatility: current ATR(14) vs 50-period average ATR (high/normal/low)
    * Momentum: RSI(14) value and direction (rising/falling/flat)
    * Range vs Trending: ADX value if computable, or use EMA slope as proxy
      (if |EMA50 slope| > threshold → trending, else → ranging)
  — Return:
    {
      "symbol": symbol, "regime": "TRENDING_BULL" | "TRENDING_BEAR" | "RANGING" | "VOLATILE",
      "trend": "bullish" | "bearish" | "neutral",
      "volatility": "high" | "normal" | "low",
      "momentum": "rising" | "falling" | "flat",
      "rsi": float, "atr_ratio": float,
      "recommended_strategy": "SWING" | "SCALPING" | "AVOID",
      "reasoning": str
    }

- `select_strategy_for_pair(symbol: str) -> str`
  — Call detect_regime()
  — TRENDING + normal volatility → "SWING"
  — RANGING + low volatility → "SCALPING"
  — VOLATILE (ATR > 2× average) → "AVOID" (return "AVOID")
  — Return strategy string

- `get_regime_for_all_pairs() -> dict[str, dict]`
  — Run detect_regime() for all watchlist pairs concurrently
  — Return dict keyed by symbol

Update `core/signal_engine.py`:
- In process_pair(): if strategy param is "AUTO", call MarketRegimeDetector.select_strategy_for_pair()
  and use the returned strategy
- If regime returns "AVOID": skip pair, log "SKIPPED: High volatility regime detected for {pair}"
- Add regime info to the Telegram alert: "📊 Regime: TRENDING_BULL | Strategy: SWING (auto-selected)"
```

---

## PROMPT 5.3 of 4 — News Sentiment Integration + Voice Alerts

```
Read MASTER_CONTEXT.md sections 11 (Notification System) and 14 (rules.json news_blackout_events)
before writing any code.

Create `core/news_monitor.py`. Add `aiohttp` (already in requirements.txt).
Add NEWS_API_KEY (from newsapi.org free tier) to .env.example.

─── FILE 1: core/news_monitor.py ───

Class `NewsMonitor`:
- `__init__` — load NEWS_API_KEY from env, initialize aiohttp session

- `fetch_economic_calendar(hours_ahead: int = 4) -> list[dict]`
  — Use the free ForexFactory JSON calendar (no API key needed):
    URL: https://nfs.faireconomy.media/ff_calendar_thisweek.json
  — Filter events where impact == "High" and time is within next `hours_ahead` hours
  — Return list of: {"event": str, "time_utc": datetime, "currency": str, "impact": str}
  — Cache result for 30 minutes (store in instance var with timestamp)

- `is_news_blackout(symbol: str, minutes_before: int = 30) -> tuple[bool, str]`
  — Fetch calendar (cached)
  — Check if any high-impact event is within `minutes_before` minutes
  — Match currency to symbol: EURUSD affected by EUR or USD events, XAUUSD by USD events,
    USDJPY by USD or JPY, BTCUSD not affected by forex news, NAS100/US30 by USD events
  — Return (is_blackout: bool, event_name_or_empty_string)

- `fetch_market_sentiment(symbol: str) -> dict`
  — If NEWS_API_KEY is set: call NewsAPI (newsapi.org) for recent news about the symbol
    e.g. query: "gold price" for XAUUSD, "bitcoin" for BTCUSD, "EUR USD" for EURUSD
    Endpoint: https://newsapi.org/v2/everything?q={query}&from={yesterday}&sortBy=popularity
  — Send headlines to Claude API (claude_client) with a mini-prompt:
    "Given these recent news headlines about {symbol}, rate the market sentiment:
     Respond with ONLY JSON: {"sentiment": "BULLISH"|"BEARISH"|"NEUTRAL", "score": -1.0 to 1.0, "summary": "one sentence"}"
  — Return: {"sentiment": str, "score": float, "summary": str, "headlines_count": int}
  — If no API key or request fails: return {"sentiment": "NEUTRAL", "score": 0.0, "summary": "No data"}

Update `core/signal_engine.py`:
- In process_pair(): call news_monitor.is_news_blackout(pair) BEFORE calling Claude
  If blackout: skip, log, send Telegram: "⏸ {pair} skipped — {event_name} in {N} minutes"
- Optionally: call fetch_market_sentiment() and append to Claude's user prompt:
  "Current news sentiment for {pair}: {sentiment} (score: {score}) — {summary}"

─── FILE 2: Update notifications/telegram.py — add voice alerts ───

Add method `send_voice_alert(signal: TradeSignal) -> None`:
- Build a plain-English text-to-speech script:
  "{direction} signal for {pair}. Entry at {entry}. Stop loss at {stop_loss}.
   Take profit at {take_profit_1}. Confidence {confidence} percent."
- Use Telegram's sendVoice API by generating an MP3 with gTTS (Google Text-to-Speech):
  `from gtts import gTTS`
  `tts = gTTS(text=speech_text, lang='en', slow=False)`
  `tts.save('/tmp/signal_alert.mp3')`
  Then send via python-telegram-bot: `await bot.send_voice(chat_id=..., voice=open('/tmp/signal_alert.mp3', 'rb'))`
- Add VOICE_ALERTS_ENABLED (true/false) to .env.example
- Only call send_voice_alert() if VOICE_ALERTS_ENABLED=true

Add `gTTS>=2.5` to requirements.txt.
```

---

## PROMPT 5.4 of 4 — Final Integration + MASTER_CONTEXT Update

```
This is the final integration prompt. Read ALL files created across Phase 1-5 before writing.

─── TASK 1: Wire all Phase 5 components into the main flow ───

Update `main.py`:
- Import and initialize: ConsensusEngine, FeedbackLoop, MarketRegimeDetector, NewsMonitor
- Pass them into SignalEngine via constructor injection (add optional params with defaults)
- Add new APScheduler job: run news_monitor.fetch_economic_calendar() every 30 minutes
  to keep the cache warm
- Add new APScheduler job: run market_regime_detector.get_regime_for_all_pairs() every
  1 hour and cache results in a module-level dict for dashboard display

─── TASK 2: Add Phase 5 API endpoints to api/routes.py ───

- GET `/ai/regime` — return market regime for all pairs (from cached results)
- GET `/ai/regime/{symbol}` — real-time regime detection for a specific pair
- GET `/ai/sentiment/{symbol}` — fetch news sentiment for a symbol
- GET `/ai/news-calendar` — return upcoming high-impact news events (next 24h)
- GET `/ai/performance-context` — return the feedback loop performance summary string
- GET `/ai/consensus-stats` — return Claude vs GPT agreement rate from database logs

─── TASK 3: Add Phase 5 section to dashboard ───

Add an 8th section "AI Intelligence" to the sidebar:

Layout:
- Market Regime panel: card grid, one per pair
  Each card: Pair | Regime badge | Strategy recommendation | ATR ratio | RSI
  Regime badge colors: TRENDING_BULL=green, TRENDING_BEAR=red, RANGING=amber, VOLATILE=red (pulse animation)
- News Sentiment strip: horizontal scrollable row of sentiment badges per pair
  Each badge: Pair | BULLISH/BEARISH/NEUTRAL with color + score bar
- Economic Calendar table: next 4 high-impact events
  Columns: Time (relative: "in 2h 15m") | Event | Currency | Impact | Affected Pairs
  Highlight rows where a signal is pending for an affected pair
- Consensus Stats card (shown only if CONSENSUS_MODE=CONSENSUS):
  Total signals | Claude+GPT agreed | Claude+GPT disagreed | Agreement rate %
  Bar chart: agreement rate over last 7 days

─── TASK 4: Final MASTER_CONTEXT.md update ───

Update MASTER_CONTEXT.md:
1. Check off ALL Phase 5 items in Section 19
2. Add Section 20: "Phase 5 — Advanced AI Features" documenting:
   - ConsensusEngine: how it works, CONSENSUS_MODE values, agreement logic
   - FeedbackLoop: how performance data feeds back into Claude's prompt
   - MarketRegimeDetector: the 4 regimes and how strategy is auto-selected
   - NewsMonitor: economic calendar source (ForexFactory), NewsAPI sentiment, voice alerts
3. Update Section 3 (Tech Stack) with new dependencies:
   openai, gTTS, vectorbt, pandas, numpy, scipy, cryptography
4. Update Section 4 (File Structure) with all new folders and files:
   backtesting/, multi_account/, and new files in core/

─── TASK 5: Final requirements.txt ───

Generate a complete, final requirements.txt with ALL dependencies across all 5 phases,
pinned to compatible stable versions as of mid-2026. Include a comment header grouping them:
# Core, # MT5, # AI, # API, # Database, # Notifications, # Backtesting, # Multi-Account, # Testing
```
