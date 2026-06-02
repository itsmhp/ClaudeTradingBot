# Phase 1 Completion Prompt — GitHub Copilot
> Paste setiap bagian secara berurutan ke Copilot Chat

---

## PROMPT 1 of 6 — MT5 Bridge

```
Read MASTER_CONTEXT.md sections 7 (MT5 Integration) and 10 (Risk Management) fully before writing any code.

Implement `core/mt5_bridge.py` in full. This file handles ALL MetaTrader5 operations for the ClaudeTradingBot project connected to an Exness broker.

Requirements:
- Class `MT5Bridge` with async-compatible methods (run blocking MT5 calls via asyncio.to_thread)
- `connect()` — initialize MT5 terminal, login using MT5_LOGIN, MT5_PASSWORD, MT5_SERVER from env, raise ConnectionError with mt5.last_error() on failure
- `disconnect()` — call mt5.shutdown() cleanly
- `get_account_info()` — return balance, equity, margin, free_margin, currency, leverage as a typed dict
- `get_symbol_info(symbol: str)` — return spread, digits, volume_min, volume_max, volume_step, point, contract_size; call mt5.symbol_select() if not visible
- `get_current_price(symbol: str)` — return bid, ask, spread from mt5.symbol_info_tick()
- `place_pending_order(signal: TradeSignal, lot_size: float)` — build the MT5 request dict using TRADE_ACTION_PENDING, map signal.order_type to mt5.ORDER_TYPE_*, use ORDER_TIME_GTC, ORDER_FILLING_IOC, magic number from rules.json instrument_config, comment "CTB_{strategy}_{direction}", call mt5.order_send(), handle ALL retcodes from MASTER_CONTEXT section 7 error table
- `get_open_positions(symbol: str | None)` — return list of open positions, optionally filtered by symbol
- `get_pending_orders(symbol: str | None)` — return list of pending orders
- `cancel_order(ticket: int)` — cancel a pending order by ticket
- `get_daily_deals()` — return all deals from today using mt5.history_deals_get(), filter by magic numbers from rules.json

Rules:
- Never log MT5_PASSWORD even at DEBUG level
- Always check mt5.initialize() return before mt5.login()
- Wrap every mt5 call in try/except, log errors with loguru
- Use TYPE_CHECKING import for TradeSignal to avoid circular imports
- All methods must have full type hints and docstrings
- Import settings from python-dotenv, not hardcoded values
```

---

## PROMPT 2 of 6 — Claude AI Client

```
Read MASTER_CONTEXT.md sections 8 (Claude AI Integration) and 9 (Signal Schema) fully before writing any code.

Implement `core/claude_client.py` in full. This file wraps the Anthropic API and produces validated TradeSignal objects.

Requirements:
- Class `ClaudeClient`
- `__init__` — initialize `anthropic.Anthropic(api_key=...)` from env ANTHROPIC_API_KEY, load rules.json from strategies/rules.json, store CLAUDE_MODEL from env
- `analyze_chart(pair: str, timeframe: str, chart_data: dict) -> TradeSignal | NoTradeSignal` — async method that:
  1. Builds the system prompt exactly as specified in MASTER_CONTEXT section 8 (role, output format, NO_TRADE format)
  2. Builds the user prompt injecting pair, timeframe, and all chart_data fields (price, rsi, macd_line, signal_line, histogram, ema_50, ema_200, structure, support_levels, resistance_levels, spread, and the full rules.json content as JSON)
  3. Calls `client.messages.create()` with model from env, max_tokens=1000, system prompt, user message
  4. Parses the response text as JSON (strip markdown fences if present)
  5. If response contains "NO_TRADE" key → return NoTradeSignal
  6. Else → return TradeSignal.model_validate(parsed_json), let ValidationError propagate as a logged warning (not crash)
- `scan_watchlist(pairs: list[str], timeframe: str, chart_data_map: dict[str, dict]) -> list[TradeSignal | NoTradeSignal]` — call analyze_chart for each pair, collect results, skip pairs where analyze_chart raises an exception
- `build_daily_briefing(chart_data_map: dict[str, dict]) -> str` — call Claude once with all pairs' data and ask for a plain-text morning briefing, return the text string

Rules:
- Cost control: never set max_tokens above 1000 (as per .env.example CAP)
- Always wrap API calls in try/except anthropic.APIError, log with loguru, re-raise
- Parse JSON safely: use json.loads inside try/except json.JSONDecodeError, log the raw response on failure
- The TradeSignal and NoTradeSignal models must be imported from core/signal_engine.py (where Pydantic models live)
- Full type hints and docstrings on all methods
```

---

## PROMPT 3 of 6 — Risk Manager + Signal Engine

```
Read MASTER_CONTEXT.md sections 9 (Signal Schema), 10 (Risk Management), and the full architecture diagram in section 2 before writing any code.

Implement TWO files:

─── FILE 1: core/risk_manager.py ───

Class `RiskManager`:
- `__init__` — load RISK_PER_TRADE_PCT, DEFAULT_RR_RATIO, MAX_DAILY_LOSS_PCT, MAX_TOTAL_POSITIONS, MAX_POSITIONS_PER_PAIR from env; load spread caps and instrument_config from strategies/rules.json
- `calculate_lot_size(symbol: str, entry_price: float, stop_loss: float, account_equity: float) -> float` — implement the formula from MASTER_CONTEXT section 10 exactly: (equity × risk_pct/100) / (sl_distance_points × point_value_per_lot), round down to instrument's volume_step, clamp between volume_min and volume_max, return the lot size
- `validate_signal(signal: TradeSignal) -> tuple[bool, str]` — check: (1) rr_ratio >= DEFAULT_RR_RATIO, (2) spread <= exness_spread_caps for symbol, (3) confidence >= 60. Return (True, "") or (False, reason_string)
- `check_daily_loss(mt5_bridge: MT5Bridge) -> tuple[bool, float]` — get today's deals via mt5_bridge.get_daily_deals(), sum profits, compare against MAX_DAILY_LOSS_PCT × starting_equity. Return (is_limit_breached, current_loss_pct)
- `check_position_limits(symbol: str, mt5_bridge: MT5Bridge) -> tuple[bool, str]` — check total open positions <= MAX_TOTAL_POSITIONS, and per-symbol positions <= MAX_POSITIONS_PER_PAIR. Return (can_trade, reason)

─── FILE 2: core/signal_engine.py ───

This file contains:
1. The Pydantic models (copy EXACTLY from MASTER_CONTEXT section 9): Direction, OrderType, Strategy, Timeframe, TradeSignal, NoTradeSignal — do not simplify or omit any validators
2. Class `SignalEngine` — the main orchestrator:
   - `__init__` — instantiate MT5Bridge, ClaudeClient, RiskManager; load rules.json; read BOT_MODE from env
   - `process_pair(pair: str, timeframe: str, strategy: str) -> dict` — full pipeline:
     1. Get current price and symbol info from MT5Bridge
     2. Package chart_data dict for ClaudeClient
     3. Call claude_client.analyze_chart()
     4. If NoTradeSignal → log and return {"result": "NO_TRADE", "reasoning": ...}
     5. Call risk_manager.validate_signal() → if invalid, log reason, send Telegram warning, return {"result": "REJECTED", "reason": ...}
     6. Check position limits → if exceeded, return {"result": "SKIPPED", "reason": ...}
     7. Calculate lot size via risk_manager.calculate_lot_size()
     8. Log signal to database via database/queries.py save_signal()
     9. If BOT_MODE == "AUTO_EXECUTE": call mt5_bridge.place_pending_order(), log execution to database
     10. Send Telegram alert via notifications/telegram.py
     11. Return {"result": "SIGNAL" or "EXECUTED", "signal": signal.model_dump(), "lot_size": lot_size}
   - `scan_all_pairs(strategy: str) -> list[dict]` — iterate watchlist from rules.json, call process_pair for each, return all results
   - `get_bot_status() -> dict` — return current state dict matching the /status API response schema from MASTER_CONTEXT section 13

Rules:
- Signal models must be importable from core.signal_engine by other modules
- All methods async, use asyncio.gather where parallel execution is safe
- Never raise unhandled exceptions from process_pair — catch, log, return {"result": "ERROR", "error": str(e)}
```

---

## PROMPT 4 of 6 — Notifications + Database

```
Read MASTER_CONTEXT.md sections 11 (Notification System) and 12 (Database Schema) fully before writing any code.

Implement THREE files:

─── FILE 1: notifications/telegram.py ───

Class `TelegramNotifier`:
- `__init__` — initialize python-telegram-bot Application from TELEGRAM_BOT_TOKEN env, store TELEGRAM_CHAT_ID
- `send_signal_alert(signal: TradeSignal, lot_size: float, execution_result: dict | None, bot_mode: str) -> None` — format and send the exact Telegram message template from MASTER_CONTEXT section 11. BUY signals use 🟢, SELL signals use 🔴. Show "✅ EXECUTING" + Order # if AUTO_EXECUTE and execution_result has order_id. Show "📡 SIGNAL ONLY" if SIGNAL_ONLY mode.
- `send_no_trade_alert(pair: str, timeframe: str, reasoning: str) -> None` — send a brief ℹ️ message
- `send_error_alert(component: str, error: str) -> None` — send a ⚠️ error message
- `send_daily_summary(performance: dict) -> None` — send end-of-day P&L summary
- `send_bot_paused(reason: str) -> None` — send 🛑 bot paused alert

─── FILE 2: database/models.py ───

Define SQLAlchemy 2.0 ORM models for ALL four tables from MASTER_CONTEXT section 12:
- `TradeSignalRecord` → maps to trade_signals table
- `ExecutedTradeRecord` → maps to executed_trades table  
- `BotLogRecord` → maps to bot_log table
- `PerformanceSummaryRecord` → maps to performance_summary table

Use DeclarativeBase, mapped_column, String, Float, Integer, Boolean, DateTime, ForeignKey with exact column names and constraints from the SQL DDL in MASTER_CONTEXT section 12.

─── FILE 3: database/db.py and database/queries.py ───

db.py:
- `init_db()` — create engine from DATABASE_URL env, create all tables via Base.metadata.create_all()
- `get_session()` — async context manager returning AsyncSession
- Engine must use check_same_thread=False for SQLite

queries.py — async functions using parameterized queries only (no string formatting):
- `save_signal(session, signal: TradeSignal) -> str` — insert into trade_signals, return signal_id
- `save_execution(session, signal_id: str, order_result: dict, lot_size: float) -> int` — insert into executed_trades
- `get_recent_signals(session, limit: int = 20) -> list` — query trade_signals ordered by created_at DESC
- `get_today_performance(session) -> dict` — aggregate trades for today: count, wins, losses, net_pnl, win_rate
- `log_event(session, level: str, component: str, message: str, details: str | None) -> None` — insert into bot_log
- `update_trade_status(session, mt5_order_id: int, status: str, profit: float | None) -> None` — update executed_trades

Rules:
- All database queries must use parameterized queries (SQLAlchemy ORM or text() with bindparams) — never f-string SQL
- Use loguru for all logging inside these files
```

---

## PROMPT 5 of 6 — FastAPI + Main Entry Point

```
Read MASTER_CONTEXT.md section 13 (API Endpoints) and section 4 (File Structure) fully before writing any code.

Implement THREE files:

─── FILE 1: api/schemas.py ───

Pydantic v2 request/response models for ALL endpoints from MASTER_CONTEXT section 13:
- `HealthResponse` — status, uptime_seconds, mt5_connected, mcp_connected, bot_mode, bot_state, timestamp
- `StatusResponse` — mode, state, active_positions, pending_orders, daily_pnl, daily_pnl_percent, daily_signals_count, account_equity, last_scan_at, next_scan_at
- `ExecuteRequest` — pair, timeframe, strategy (with Literal type for strategy values)
- `PauseResumeResponse` — success, message, state
- `PerformanceResponse` — period, total_signals, executed_trades, win_rate, net_pnl, profit_factor, avg_rr_achieved, max_drawdown_percent, best_trade, worst_trade
- `SignalListResponse` — items list, total, page, page_size

─── FILE 2: api/routes.py ───

FastAPI APIRouter with ALL routes from MASTER_CONTEXT section 13:
- GET /health → HealthResponse
- GET /status → StatusResponse
- GET /signals → SignalListResponse (query params: page=1, page_size=20)
- GET /signals/{signal_id} → TradeSignal dict or 404
- GET /trades → list (query params: page, page_size)
- GET /trades/{trade_id} → dict or 404
- POST /execute → runs signal_engine.process_pair() with request body, returns result dict
- POST /pause → sets bot state to PAUSED, returns PauseResumeResponse
- POST /resume → sets bot state to RUNNING, returns PauseResumeResponse
- GET /performance → PerformanceResponse (query param: period=today|week|month|all)
- GET /performance/today → today's performance from database

Each route must use async def, dependency injection for DB session (Depends(get_session)), proper HTTP status codes (404 for not found, 422 for validation errors auto-handled by FastAPI).

─── FILE 3: main.py (root entry point) ───

- Load .env with python-dotenv at startup
- Initialize database (call init_db())
- Connect MT5Bridge on startup
- Create FastAPI app with title "ClaudeTradingBot API", version "1.0.0"
- Include api/routes.py router
- Add APScheduler AsyncIOScheduler:
  - Job 1: scan_all_pairs("SWING") every 4 hours (H4 candle close times: 01:00, 05:00, 09:00, 13:00, 17:00, 21:00 UTC)
  - Job 2: scan_all_pairs("SCALPING") every 15 minutes during London+NY sessions (07:00–21:00 UTC)
  - Job 3: check_daily_loss() every 5 minutes
- Add startup/shutdown event handlers (lifespan context manager)
- Bind uvicorn to 127.0.0.1:8000 (localhost only — security)
- Entry point: `if __name__ == "__main__": uvicorn.run(...)` with reload=False

Rules:
- Bind to 127.0.0.1 only, never 0.0.0.0
- Add rate limiting: slowapi or manual counter, max 10 POST requests/minute to /execute
- All routes return consistent JSON error format: {"error": "message", "detail": "..."}
```

---

## PROMPT 6 of 6 — Tests + End-to-End Verification

```
Read MASTER_CONTEXT.md section 18 (Testing Plan) fully before writing any code.

Implement the test suite and verify the full pipeline.

─── FILE 1: tests/conftest.py ───

Pytest fixtures:
- `mock_mt5` — patches the MetaTrader5 module with mock objects. Set TRADE_RETCODE_DONE=10009, ORDER_TYPE_BUY_LIMIT=2, ORDER_TYPE_SELL_LIMIT=3, ORDER_TYPE_BUY_STOP=4, ORDER_TYPE_SELL_STOP=5. mock account_info() returns balance=10000, equity=10000, currency="USD". mock symbol_info("XAUUSD") returns spread=12, volume_min=0.01, volume_max=100, volume_step=0.01, point=0.01, contract_size=100, digits=2. mock order_send() returns retcode=10009, order=12345.
- `sample_buy_signal` — a valid TradeSignal fixture for XAUUSD BUY LIMIT, entry=2350, sl=2340, tp1=2370, tp2=2390, confidence=78
- `sample_sell_signal` — a valid TradeSignal fixture for EURUSD SELL LIMIT
- `test_db` — in-memory SQLite session for database tests
- `mock_anthropic` — patches anthropic.Anthropic to return a mock response with valid JSON matching sample_buy_signal

─── FILE 2: tests/test_mt5_bridge.py ───

Using mock_mt5 fixture:
- `test_connect_success` — connect() returns True, account info is accessible
- `test_connect_failure` — mt5.initialize() returns False → raises ConnectionError
- `test_place_buy_limit_order` — place_pending_order with BUY_LIMIT signal, verify request dict has correct action, type, magic number
- `test_place_order_retcode_error` — mt5.order_send() returns retcode=10006 → returns dict with success=False
- `test_get_positions_empty` — positions_get returns None → method returns empty list
- `test_spread_check` — get_symbol_info returns spread above cap → validate_signal in risk_manager returns False

─── FILE 3: tests/test_signal_engine.py ───

- `test_valid_buy_signal_parsed` — TradeSignal.model_validate() on valid dict succeeds
- `test_invalid_sl_buy_rejected` — BUY with sl > entry raises ValidationError
- `test_invalid_sl_sell_rejected` — SELL with sl < entry raises ValidationError
- `test_low_confidence_rejected` — confidence=45 raises ValidationError
- `test_rr_ratio_calculated` — verify risk_reward_ratio property: entry=2350, sl=2340, tp1=2370 → ratio=2.0
- `test_no_trade_signal_parsed` — NoTradeSignal with signal="NO_TRADE" and reasoning parses correctly

─── FILE 4: tests/test_risk_manager.py ───

- `test_lot_size_xauusd` — equity=10000, risk=1%, entry=2350, sl=2340 → lot_size=0.10
- `test_lot_size_eurusd` — equity=10000, risk=1%, entry=1.08500, sl=1.08400 → verify correct calculation
- `test_lot_size_rounds_to_step` — result must be multiple of volume_step
- `test_lot_size_clamped_to_min` — very small account → lot clamped to volume_min
- `test_validate_signal_passes` — valid signal with rr>=2.0 and spread within cap → (True, "")
- `test_validate_signal_fails_rr` — signal with rr=1.5 → (False, reason containing "R:R")
- `test_validate_signal_fails_spread` — spread=50 for XAUUSD (cap=30) → (False, reason containing "spread")

After writing all tests, run: `pytest tests/ -v --tb=short`
Report which tests pass and which fail. For any failures, fix the implementation (not the tests) to make them pass.
```
