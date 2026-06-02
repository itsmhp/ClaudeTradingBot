# Claude Code × TradingView — MCP Setup Reference Guide
> By Ishaan Agarwal (@ishaan_576) · LevelPrep.org · Vol VI / 2026
> Original PDF: "Claude Code x TradingView Setup"

---

## What This Is

The TradingView MCP server is an open-source bridge that connects Claude Code directly to
TradingView Desktop. Once connected, Claude can read your live charts, pull indicator values,
scan your watchlist, and act on what it sees — all from a single conversation.

No API keys, no third-party data subscriptions, no Python scripts to maintain.
Just Claude and your existing TradingView setup talking to each other.

The setup takes one prompt. The prompt installs everything, writes the trading config,
and verifies the connection end to end.

---

## Prerequisites

Two things required before starting:

1. **TradingView Desktop** — the desktop app, NOT the browser version.
   Download at: https://tradingview.com/desktop

2. **Claude Code** — Anthropic's agentic CLI tool.
   Install at: https://claude.ai/claude-code

---

## How It Works

TradingView Desktop exposes a **Chrome DevTools Protocol (CDP) port** when launched with a
debug flag. The MCP server connects to that port and gives Claude structured access to
everything on screen. Claude reads it, reasons about it, and responds in plain English.

**The connection is local — your data never leaves your machine.**

---

## The Master Setup Prompt (paste into Claude Code)

```
You are going to set up the TradingView MCP server and connect it to this Claude Code session.
Work through each step in order. If a step fails, stop and report the exact error — do not proceed
past a failure.

STEP 1: Install the MCP server.
Clone https://github.com/tradesdontlie/tradingview-mcp.git into ~/tradingview-mcp and run npm
install inside it. If the directory already exists, pull the latest changes instead of recloning.

STEP 2: Register the MCP server.
Edit ~/.claude/.mcp.json to add the tradingview server entry below. If other MCP servers already
exist in the file, merge this entry into the existing mcpServers object without overwriting
anything else. Replace <HOME> with the actual absolute path to the home directory.

{ "mcpServers": { "tradingview": { "command": "node", "args": ["<HOME>/tradingview-mcp/src/server.js"] } } }

STEP 3: Write the trading configuration.
Create ~/tradingview-mcp/rules.json with a swing-trading config covering:
- A watchlist of major crypto pairs and macro dominance charts
- Three timeframes (weekly, daily, 4H)
- Clear bullish and bearish bias criteria using EMA position, RSI range, and market structure
- Risk rules including max 1% risk per trade and a minimum 2:1 reward-to-risk ratio
- A list of key indicators: RSI 14, MACD 12/26/9, 50 EMA, 200 EMA, Volume

STEP 4: Launch TradingView with the debug port.
Use tv_launch if available. Otherwise detect the TradingView Desktop app on this machine and
launch it with --remote-debugging-port=9222.
Standard paths:
- Mac: /Applications/TradingView.app/Contents/MacOS/TradingView
- Windows: %LOCALAPPDATA%\TradingView\TradingView.exe

STEP 5: Verify the connection.
Run tv_health_check and confirm cdp_connected returns true. Then report the full setup status:
MCP installed and connected, rules file path, TradingView connected on port 9222, and whether the
session is ready to use.
```

---

## What Claude Can Do Once Connected

| # | Feature | Description |
|---|---------|-------------|
| 01 | **Full Chart Analysis** | Reads price, RSI, MACD, EMA positions, recent structure → gives plain-English bias: bullish / bearish / neutral with reasoning |
| 02 | **Watchlist Scanner** | Scans all pairs in config, checks against bias criteria, returns ranked list of cleanest setups |
| 03 | **Trade Setup Generator** | Reads chart, identifies entry zones, stop placement, TP targets, validates 2:1 RR rule |
| 04 | **Position Sizer** | Calculates exact position size to risk configured 1% per trade, accounting for stop distance and fees |
| 05 | **Macro Context Read** | Pulls TOTAL, TOTAL3, BTC.D charts and advises: new longs / sit in cash / rotate to dominance plays |
| 06 | **Daily Briefing** | Scans full watchlist, reads macro dominance, checks high-impact events → structured daily plan in <60 seconds |

---

## Example rules.json (Swing Trading Config for Crypto)

```json
{
  "watchlist": {
    "majors": ["BINANCE:BTCUSDT", "BINANCE:ETHUSDT", "BINANCE:SOLUSDT"],
    "alts": ["BINANCE:LINKUSDT", "BINANCE:AVAXUSDT", "BINANCE:SUIUSDT"],
    "macro": ["CRYPTOCAP:TOTAL", "CRYPTOCAP:TOTAL3", "CRYPTOCAP:BTC.D"]
  },
  "timeframes_to_check": ["1W", "1D", "4H"],
  "bias_criteria": {
    "bullish": "Price above 50D EMA, RSI 45-70, higher highs and higher lows on 4H",
    "bearish": "Price below 50D EMA, RSI below 45, lower highs and lower lows on 4H",
    "neutral": "Price chopping around 50D EMA, RSI 40-60, no clear structure"
  },
  "risk_rules": {
    "max_risk_per_trade": "1% of portfolio",
    "min_rr_ratio": 2,
    "no_trades_during": ["major US CPI", "FOMC", "weekend thin liquidity"]
  },
  "indicators": ["RSI (14)", "MACD (12, 26, 9)", "50 EMA", "200 EMA", "Volume"]
}
```

### What Each Field Does

- `watchlist` — what Claude scans
- `timeframes_to_check` — how deep it looks
- `bias_criteria` — logic it uses to call bullish, bearish, or neutral
- `risk_rules` — governs every trade setup it generates
- `indicators` — which values to pull from each chart

You can write these in plain English — Claude understands it.

---

## Example Prompts (Once Connected)

```
"Scan my full watchlist and tell me which pairs have the cleanest setups right now."
"Read the BTC daily chart. What's the current bias based on my rules?"
"ETH is at support on the 4H. Give me a long setup with entry, stop, and two take-profit levels."
"My account is $10,000. I want to short SOL with a stop 3% above entry. What size should I take?"
"Check BTC dominance and TOTAL3. Should I be in alts right now or waiting?"
"Give me a morning briefing. What's setting up today across majors and alts?"
"Has LINK broken structure on the daily? Is the trend still intact?"
"Flag any pairs on my watchlist that are approaching key levels in the next 24 hours."
```

---

## Resources & Credits

- **MCP Server (open source):** https://github.com/tradesdontlie/tradingview-mcp
- **TradingView Desktop:** https://tradingview.com/desktop
- **Claude Code:** https://claude.ai/claude-code

> The TradingView MCP server was built by @tradesdontlie on GitHub.
> This guide packages the setup into a single prompt and adds the swing-trading configuration layer.
> All credit for the underlying MCP goes to the original author.

---

*Source: "Claude Code x TradingView" by Ishaan Agarwal (@ishaan_576), LevelPrep.org, Vol VI / 2026*
