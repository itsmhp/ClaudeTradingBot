# MCP Setup — TradingView Desktop Integration

## Prerequisites

- Google Chrome or Chromium installed
- Node.js 18+ installed
- TradingView Desktop app (or tradingview.com open in Chrome)

## Step 1 — Clone tradingview-mcp

```bash
git clone https://github.com/tradesdontlie/tradingview-mcp
cd tradingview-mcp
npm install
npm run build
```

## Step 2 — Launch TradingView with CDP enabled

```bash
# Windows
"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" \\
  --remote-debugging-port=9222 \\
  --user-data-dir=C:\\temp\\chrome-debug \\
  https://www.tradingview.com/chart/

# macOS
/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
  --remote-debugging-port=9222 \\
  --user-data-dir=/tmp/chrome-debug \\
  https://www.tradingview.com/chart/
```

## Step 3 — Register in Claude MCP config

Create or edit `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "node",
      "args": ["/path/to/tradingview-mcp/dist/index.js"],
      "env": {
        "CDP_URL": "http://localhost:9222"
      }
    }
  }
}
```

## Step 4 — Verify

In Claude Code, run:
```
/mcp
```
You should see `tradingview` listed as a connected server.

## Environment Variable

```
TRADINGVIEW_CDP_PORT=9222   # in .env
```
