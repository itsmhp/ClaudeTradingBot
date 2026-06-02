# Copilot Instructions — ClaudeTradingBot

> **Intended location:** `.github/copilot-instructions.md`
> Move this file to `.github/copilot-instructions.md` once the directory is created.

> This file tells GitHub Copilot everything it needs to know to assist on this project.

---

## Project Purpose

ClaudeTradingBot is an AI-powered local trading system that:
1. Connects to **TradingView Desktop** via MCP (Model Context Protocol) over CDP (Chrome DevTools Protocol, port 9222)
2. Uses **Claude AI** (Anthropic API) to analyze charts and generate structured trade signals
3. Executes pending orders on **MetaTrader 5** connected to an **Exness** broker account
4. Sends alerts via **Telegram** and logs everything to a **SQLite** database
5. Exposes a **FastAPI** REST API for monitoring and control

Two modes: `SIGNAL_ONLY` (alerts only) and `AUTO_EXECUTE` (places pending orders).

---

## Architecture Summary

```
TradingView Desktop → CDP:9222 → tradingview-mcp (Node.js) → Claude API
    → Signal Engine (Python) → Risk Manager → MT5 Bridge → Exness Terminal
    → Telegram Notification + SQLite Logging + FastAPI Status
```

---

## Language and Framework Conventions

### Python 3.11+
- Use type hints on ALL function signatures and class attributes
- Use `match` statements where appropriate (Python 3.10+ pattern matching)
- Use f-strings for string formatting (never `.format()` or `%`)
- Use `pathlib.Path` for file paths, not `os.path`
- Use `datetime.datetime` with UTC timezone always (`datetime.now(timezone.utc)`)

### FastAPI
- All route handlers must be `async def`
- Use dependency injection for shared resources (MT5 connection, DB session)
- Use `Depends()` for authentication and rate limiting
- Response models must be Pydantic `BaseModel` subclasses
- Use `status_code` parameter explicitly on all routes
- Use `HTTPException` for error responses with appropriate status codes

### Pydantic v2
- Always use `BaseModel` (not dataclasses) for data structures
- Use `Field(...)` with descriptions for all model fields
- Use `field_validator` (not `validator` — that's v1)
- Use `model_validate()` and `model_validate_json()` for parsing
- Use `model_dump()` (not `.dict()` — that's v1)
- Define `model_config = ConfigDict(...)` instead of inner `class Config`

### async/await
- All I/O operations must be async (HTTP calls, database queries, file writes)
- Use `asyncio.gather()` for concurrent independent operations
- Use `asyncio.to_thread()` for blocking MT5 calls (MT5 library is synchronous)
- Never use `time.sleep()` — use `await asyncio.sleep()`

---

## MT5-Specific Coding Conventions

### Always check return codes
```python
result = mt5.order_send(request)
if result is None:
    raise MT5Error(f"order_send returned None: {mt5.last_error()}")
if result.retcode != mt5.TRADE_RETCODE_DONE:
    raise MT5Error(f"Order failed: {result.retcode} - {result.comment}")
```

### Always use ORDER_FILLING_IOC for Exness
```python
request = {
    "action": mt5.TRADE_ACTION_PENDING,
    "type_filling": mt5.ORDER_FILLING_IOC,  # REQUIRED for Exness
    ...
}
```

### Always initialize before any operation
```python
if not mt5.initialize():
    raise ConnectionError(f"MT5 init failed: {mt5.last_error()}")
```

### Use magic numbers to identify bot orders
```python
MAGIC_NUMBER = 234000  # Base magic for this bot
# Per-instrument: 234001 (XAUUSD), 234002 (BTCUSD), etc.
```

### Wrap MT5 calls in asyncio.to_thread
```python
async def get_positions():
    return await asyncio.to_thread(mt5.positions_get)
```

---

## Claude API Conventions

### Always use the specified model
```python
model = "claude-sonnet-4-20250514"
```

### Always set max_tokens
```python
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1000,  # Never exceed 1000 for signal generation
    system=system_prompt,
    messages=[{"role": "user", "content": user_prompt}]
)
```

### Always expect and parse structured JSON responses
```python
# Parse Claude's response as JSON
try:
    signal_data = json.loads(response.content[0].text)
    signal = TradeSignal.model_validate(signal_data)
except (json.JSONDecodeError, ValidationError) as e:
    logger.error(f"Failed to parse Claude response: {e}")
    log_invalid_response(response.content[0].text)
```

### System prompt must enforce JSON output format
The system prompt must explicitly state the expected JSON schema and instruct Claude to respond with ONLY valid JSON.

---

## File Structure Awareness

| Concern | Location |
|---------|----------|
| Signal generation orchestration | `core/signal_engine.py` |
| MT5 connection and orders | `core/mt5_bridge.py` |
| Claude API communication | `core/claude_client.py` |
| Position sizing and risk | `core/risk_manager.py` |
| Scalping strategy logic | `strategies/scalping.py` |
| Swing trading strategy logic | `strategies/swing.py` |
| Trading rules config | `strategies/rules.json` |
| Telegram notifications | `notifications/telegram.py` |
| Webhook notifications | `notifications/webhook.py` |
| FastAPI app and routes | `api/main.py`, `api/routes.py` |
| API request/response models | `api/schemas.py` |
| Test files | `tests/test_*.py` |
| App entry point | `main.py` |
| Environment config | `.env` (gitignored), `.env.example` |

---

## What NOT To Do

### ❌ No hardcoded credentials
```python
# BAD
api_key = "sk-ant-api03-..."
mt5_password = "mypassword"

# GOOD
api_key = os.getenv("ANTHROPIC_API_KEY")
mt5_password = settings.mt5_password
```

### ❌ No blocking calls in async context
```python
# BAD
async def scan_chart():
    result = mt5.positions_get()  # BLOCKS the event loop!

# GOOD
async def scan_chart():
    result = await asyncio.to_thread(mt5.positions_get)
```

### ❌ No print() in production code
```python
# BAD
print(f"Signal generated: {signal}")

# GOOD
from loguru import logger
logger.info(f"Signal generated: {signal.signal_id}", pair=signal.pair)
```

### ❌ No bare except clauses
```python
# BAD
try:
    result = mt5.order_send(request)
except:
    pass

# GOOD
try:
    result = mt5.order_send(request)
except Exception as e:
    logger.error(f"Order send failed: {e}", exc_info=True)
    raise MT5OrderError(f"Failed to send order: {e}") from e
```

### ❌ No mutable default arguments
```python
# BAD
def process_signals(signals: list = []):
    ...

# GOOD
def process_signals(signals: list | None = None):
    signals = signals or []
```

### ❌ No market orders
```python
# BAD — We NEVER place market orders
request = {"action": mt5.TRADE_ACTION_DEAL, ...}

# GOOD — Only pending orders
request = {"action": mt5.TRADE_ACTION_PENDING, ...}
```

---

## Pydantic Model Patterns

### Standard model structure
```python
from pydantic import BaseModel, Field, field_validator, ConfigDict
from datetime import datetime, timezone
from typing import Optional

class TradeSignal(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, frozen=False)

    pair: str = Field(..., description="Trading instrument symbol")
    entry_price: float = Field(..., gt=0, description="Entry price")
    confidence: int = Field(..., ge=0, le=100)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("pair")
    @classmethod
    def pair_must_be_valid(cls, v: str) -> str:
        valid_pairs = {"XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "NAS100", "US30"}
        if v.upper() not in valid_pairs:
            raise ValueError(f"Invalid pair: {v}")
        return v.upper()
```

### Settings model (loaded from .env)
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str
    mt5_login: int
    mt5_password: str
    mt5_server: str
    bot_mode: str = "SIGNAL_ONLY"
    risk_per_trade_pct: float = 1.0
```

---

## Database Access Patterns

### Always use context managers
```python
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession

@asynccontextmanager
async def get_db_session():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

### Always use parameterized queries (no string interpolation)
```python
# BAD — SQL injection risk
query = f"SELECT * FROM trade_signals WHERE pair = '{pair}'"

# GOOD — Parameterized
stmt = select(TradeSignalModel).where(TradeSignalModel.pair == pair)
result = await session.execute(stmt)
```

### Log to database AND structured logs
```python
async def log_signal(signal: TradeSignal, session: AsyncSession):
    # Database log
    db_record = TradeSignalRecord(**signal.model_dump())
    session.add(db_record)

    # Structured log
    logger.info(
        "Signal generated",
        signal_id=signal.signal_id,
        pair=signal.pair,
        direction=signal.direction,
        confidence=signal.confidence,
    )
```

---

## Error Handling Patterns

### Custom exception hierarchy
```python
class TradingBotError(Exception):
    """Base exception for all bot errors."""

class MT5Error(TradingBotError):
    """MT5 connection or order errors."""

class SignalValidationError(TradingBotError):
    """Signal failed validation."""

class RiskLimitError(TradingBotError):
    """Risk management rule violated."""

class ClaudeAPIError(TradingBotError):
    """Claude API communication error."""
```

### Retry pattern for transient errors
```python
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def call_claude_api(prompt: str) -> dict:
    ...
```

---

## Testing Conventions

- Test files: `tests/test_<module_name>.py`
- Use `pytest` with `pytest-asyncio` for async tests
- Mock external services (MT5, Claude API, Telegram) — never call real APIs in tests
- Use fixtures in `conftest.py` for shared setup
- Test happy path, error path, and edge cases

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_signal_engine_generates_valid_signal():
    with patch("core.claude_client.generate_signal") as mock_claude:
        mock_claude.return_value = sample_signal_dict
        signal = await signal_engine.scan_pair("XAUUSD", "H4")
        assert signal.pair == "XAUUSD"
        assert signal.confidence >= 60
```
