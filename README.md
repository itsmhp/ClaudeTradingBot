# 🤖 ClaudeTradingBot

> **AI-Powered Trading System** — Claude AI analyzes TradingView charts via MCP and executes trades on MT5/Exness with precision pending orders.

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green.svg)](https://fastapi.tiangolo.com)
[![Claude AI](https://img.shields.io/badge/Claude-Sonnet%204-purple.svg)](https://anthropic.com)
[![MT5](https://img.shields.io/badge/MetaTrader5-Exness-orange.svg)](https://www.exness.com)

---

## 📖 Overview

ClaudeTradingBot is a locally-hosted trading system that connects three powerful platforms:

1. **TradingView Desktop** — Live charts, indicators, and technical analysis
2. **Claude AI (Anthropic)** — Intelligent chart reasoning and signal generation
3. **MetaTrader 5 (Exness)** — Trade execution with pending orders

The bot operates in two modes:
- **SIGNAL_ONLY** — Analyzes charts and sends Telegram alerts (no orders placed)
- **AUTO_EXECUTE** — Analyzes charts AND places pending orders on MT5 automatically

### Supported Instruments
| Category | Instruments |
|----------|-------------|
| Commodities | XAUUSD (Gold) |
| Crypto | BTCUSD |
| Forex | EURUSD, GBPUSD, USDJPY |
| Indices | NAS100, US30 |

### Strategies
- **Scalping** (M1/M5/M15) — EMA crossover + RSI + S/R levels
- **Swing Trading** (H1/H4/D1) — Structure breaks + EMA position + pullback entries

---

## 🏗️ Architecture

```
TradingView Desktop (Charts + Indicators)
    │
    ▼ CDP Port 9222 (Chrome DevTools Protocol)
    │
tradingview-mcp Server (Node.js)
    │
    ▼ MCP Tools (chart data, indicators, watchlist)
    │
Claude AI (Anthropic API) ← rules.json (trading config)
    │
    ▼ Structured JSON Signal
    │
Signal Engine (Python) → Telegram Alert
    │
    ▼ Validated + Risk-Checked
    │
MT5 Python Bridge → Exness MT5 Terminal → Pending Order
    │
    ▼
SQLite Database (trade log, signals, performance)
```

> See `MASTER_CONTEXT.md` Section 2 for the full Mermaid architecture diagram.

---

## 📋 Prerequisites

Before installation, ensure you have:

| Requirement | Version | Notes |
|------------|---------|-------|
| Python | 3.11+ | With pip |
| Node.js | 20 LTS+ | For MCP server |
| TradingView Desktop | Latest | NOT the browser version |
| MetaTrader 5 | Latest | With Exness account logged in |
| Claude Code | Latest | Anthropic's CLI tool |
| Git | Any | For cloning repos |
| Telegram Account | — | For alert notifications |

### Broker Requirements
- Active **Exness** trading account (Standard or Pro)
- MT5 terminal downloaded from Exness and logged in
- Know your MT5 login number, password, and server name

---

## 🚀 Installation

### Step 1: Clone This Repository

```bash
git clone https://github.com/yourusername/ClaudeTradingBot.git
cd ClaudeTradingBot
```

### Step 2: Create Python Virtual Environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Mac/Linux
source .venv/bin/activate
```

### Step 3: Install Python Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Install the TradingView MCP Server

```bash
cd ~
git clone https://github.com/tradesdontlie/tradingview-mcp.git
cd tradingview-mcp
npm install
```

### Step 5: Register MCP Server in Claude Code

Create or edit `~/.claude/.mcp.json` (Windows: `%USERPROFILE%\.claude\.mcp.json`):

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "node",
      "args": ["C:/Users/YOUR_USERNAME/tradingview-mcp/src/server.js"]
    }
  }
}
```

### Step 6: Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your actual values:
- Anthropic API key
- MT5 login credentials (from Exness)
- Telegram bot token (from @BotFather)
- Your Telegram chat ID

### Step 7: Copy Trading Rules

```bash
# Copy rules.json to the MCP server directory
cp strategies/rules.json ~/tradingview-mcp/rules.json
```

### Step 8: Create a Telegram Bot

1. Open Telegram and message `@BotFather`
2. Send `/newbot` and follow prompts
3. Copy the bot token to your `.env` file
4. Message `@userinfobot` to get your chat ID
5. Start a conversation with your new bot (required before it can send you messages)

### Step 9: Verify MT5 Connection

Ensure MetaTrader 5 is running and logged into your Exness account.

```bash
python -c "import MetaTrader5 as mt5; mt5.initialize(); print(mt5.account_info())"
```

### Step 10: Launch TradingView with Debug Port

**Windows:**
```cmd
"%LOCALAPPDATA%\TradingView\TradingView.exe" --remote-debugging-port=9222
```

**Or create a shortcut** with `--remote-debugging-port=9222` appended to the target.

---

## ⚙️ Configuration

### .env Setup

See `.env.example` for all available variables. Key settings:

| Variable | Description | Example |
|----------|-------------|---------|
| `BOT_MODE` | Operating mode | `SIGNAL_ONLY` or `AUTO_EXECUTE` |
| `RISK_PER_TRADE_PCT` | Risk per trade | `1.0` (= 1%) |
| `DEFAULT_RR_RATIO` | Minimum R:R ratio | `2.0` |
| `MAX_DAILY_LOSS_PCT` | Auto-pause threshold | `3.0` (= 3%) |

### rules.json Customization

Edit `strategies/rules.json` to customize:
- **Watchlist** — Add/remove instruments
- **Timeframes** — Adjust per strategy
- **Bias criteria** — Modify EMA/RSI rules
- **Session filters** — Change active trading hours
- **Spread caps** — Adjust per instrument tolerance
- **News events** — Add/remove blackout events

---

## 🎮 Usage

### Start in SIGNAL_ONLY Mode (Recommended First)

```bash
# Set in .env:
# BOT_MODE=SIGNAL_ONLY

python main.py
```

The bot will:
1. Connect to MT5 (read-only)
2. Connect to TradingView via MCP
3. Scan charts on schedule
4. Generate signals via Claude
5. Send Telegram alerts
6. Log everything to database

**No orders will be placed.**

### Start in AUTO_EXECUTE Mode

```bash
# Set in .env:
# BOT_MODE=AUTO_EXECUTE

python main.py
```

The bot will do everything above PLUS:
- Place pending orders on MT5 (buy limit, sell limit, buy stop, sell stop)
- Track order status
- Move SL to breakeven after TP1

### API Control (While Running)

```bash
# Check status
curl http://localhost:8000/status

# Pause bot
curl -X POST http://localhost:8000/pause

# Resume bot
curl -X POST http://localhost:8000/resume

# Trigger manual scan
curl -X POST http://localhost:8000/execute -H "Content-Type: application/json" \
  -d '{"pair": "XAUUSD", "timeframe": "H4", "strategy": "SWING"}'

# View performance
curl http://localhost:8000/performance?period=today
```

---

## 📱 Telegram Alerts

When a signal is generated, you'll receive a formatted message like:

```
🟢 BUY SIGNAL — XAUUSD

📊 Strategy: SWING | Timeframe: H4
📈 Direction: BUY LIMIT

💰 Entry: 2350.00
🛑 Stop Loss: 2340.00
🎯 TP1: 2370.00
🎯 TP2: 2390.00

⚖️ Risk:Reward = 1:2.0
🎲 Confidence: 78%

💡 Reasoning:
Price pulled back to 50 EMA on H4 after structure break...

🤖 Mode: ✅ EXECUTING
📋 Order #: 12345678
⏰ 2026-06-02 09:30 UTC
```

---

## 📁 Project Structure

```
ClaudeTradingBot/
├── core/              # Signal engine, MT5 bridge, Claude client, risk manager
├── strategies/        # Scalping/swing logic, rules.json
├── notifications/     # Telegram and webhook dispatchers
├── api/               # FastAPI routes and schemas
├── dashboard/         # Optional web status page
├── tests/             # Unit and integration tests
├── docs/              # Documentation and references
├── .env.example       # Environment variable template
├── requirements.txt   # Python dependencies
├── package.json       # Node.js dependencies (MCP)
├── main.py            # Application entry point
├── MASTER_CONTEXT.md  # Complete system documentation
└── README.md          # This file
```

---

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_signal_engine.py

# Run with coverage
pytest --cov=core --cov-report=html
```

---

## ⚠️ Disclaimer

> **IMPORTANT: Trading involves substantial risk of loss and is not suitable for all investors.**

- This software is provided "as is" without warranty of any kind
- This is **NOT financial advice** — it is a technical tool for educational purposes
- Past performance does not guarantee future results
- You are solely responsible for your trading decisions
- The authors are not liable for any financial losses incurred through use of this software
- Always start with `SIGNAL_ONLY` mode and paper trading before using real funds
- Never risk money you cannot afford to lose

**By using this software, you acknowledge that you understand and accept these risks.**

---

## 📚 Documentation

- `MASTER_CONTEXT.md` — Complete system documentation (19 sections)
- `docs/claude_tradingview_mcp_reference.md` — MCP setup reference
- `.github/copilot-instructions.md` — Copilot coding conventions

---

## 📄 License

This project is for personal use. Not licensed for redistribution.

---

## 🙏 Credits

- **tradingview-mcp** by [@tradesdontlie](https://github.com/tradesdontlie/tradingview-mcp) — MCP server for TradingView
- **MCP Setup Guide** by Ishaan Agarwal ([@ishaan_576](https://twitter.com/ishaan_576)) — Original connection reference
- **Anthropic** — Claude AI API
- **MetaQuotes** — MetaTrader 5 platform
- **Exness** — Broker platform
