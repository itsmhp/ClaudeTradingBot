# Phase 2 Completion Prompt — GitHub Copilot
> Paste setiap bagian secara berurutan ke Copilot Chat

---

## PROMPT 1 of 4 — Dashboard Layout & Design System

```
Read MASTER_CONTEXT.md sections 2 (Architecture), 13 (API Endpoints), and the dashboard/ folder
definition in section 4 before writing any code.

Create the base dashboard file: `dashboard/index.html`

This is a single-file dashboard (HTML + CSS + JS in one file) that runs locally and consumes
the FastAPI backend at http://localhost:8000. No build tools, no npm, no React — pure HTML/CSS/JS.

Design requirements:
- Dark theme, trading terminal aesthetic — use a dark background (#0d1117 or similar),
  with a sidebar on the left and main content on the right
- Color coding: green (#00c853) for profit/buy signals, red (#f44336) for loss/sell signals,
  amber (#ffc107) for warnings/neutral, blue (#2196f3) for info
- Monospace font for prices and numbers (use 'JetBrains Mono' from Google Fonts or similar)
- Fully responsive but optimized for 1920×1080 desktop
- NO external JS frameworks (no React, Vue, Angular) — vanilla JS only
- Chart.js from cdnjs for charts
- Auto-refresh every 30 seconds using setInterval

Layout structure:
┌─────────────────────────────────────────────────────┐
│  HEADER: ClaudeTradingBot | Status pill | Bot Mode  │
├──────────┬──────────────────────────────────────────┤
│          │  ROW 1: 4 stat cards                     │
│          │  (Balance | Daily P&L | Win Rate |        │
│  SIDEBAR │   Active Positions)                      │
│          ├──────────────────────────────────────────┤
│  - Dashboard   │  ROW 2: P&L Chart (line) | Open    │
│  - Signals │  Positions table                       │
│  - Trades  ├──────────────────────────────────────┤
│  - Performance │  ROW 3: Recent Signals feed        │
│  - Settings│                                        │
│            │                                        │
└──────────┴──────────────────────────────────────────┘

Sections to implement in this file (as separate <section> divs, shown/hidden via sidebar nav):
1. Dashboard (default view) — stat cards + P&L chart + open positions + recent signals
2. Signals — full signal history with filters
3. Trades — executed trades table
4. Performance — win rate, P&L breakdown, equity curve
5. Settings — bot mode toggle, risk settings display, bot control buttons

In this prompt, implement ONLY:
- The complete HTML structure with all sections (initially hidden except Dashboard)
- The full CSS design system (CSS variables, all component styles, responsive grid)
- The JavaScript skeleton: section navigation, auto-refresh timer, a fetchAPI(endpoint)
  helper that calls http://localhost:8000/{endpoint} with error handling and loading states
- The header with: bot name, a colored status pill (RUNNING=green/PAUSED=amber/ERROR=red),
  and the bot mode badge (SIGNAL_ONLY=blue/AUTO_EXECUTE=orange)
- The sidebar navigation with active state highlighting

Data fetching and rendering will be added in the next prompts. For now, all data display
areas should show a subtle loading skeleton (animated grey pulse blocks).
```

---

## PROMPT 2 of 4 — Dashboard Section & Live Data

```
Read MASTER_CONTEXT.md section 13 (API Endpoints) for all response schemas before writing any code.
Continue working on `dashboard/index.html` from the previous prompt.

Implement the JavaScript data layer and render all five dashboard sections with live data
from the FastAPI backend at http://localhost:8000.

─── SECTION 1: Dashboard (main view) ───

Stat Cards — fetch GET /status every 30s and update:
- Card 1 "Account Balance" — show account_equity formatted as $X,XXX.XX
- Card 2 "Daily P&L" — show daily_pnl with + or - prefix and color (green/red)
- Card 3 "Win Rate" — fetch from GET /performance?period=today, show as X.X%
- Card 4 "Active Positions" — show active_positions / MAX (e.g. "2 / 5")

P&L Chart — fetch GET /performance?period=week, render a Chart.js line chart:
- X axis: last 7 days (date labels)
- Y axis: cumulative net P&L in USD
- Single line: green when positive, red when negative (use gradient fill)
- Tooltip shows: date, net P&L, trades count

Open Positions Table — fetch GET /status, then for each active position show:
| Pair | Direction | Entry | Current P&L | SL | TP1 | Open Since |
Color the P&L cell green/red based on value.

Recent Signals Feed — fetch GET /signals?page=1&page_size=10, render as a vertical
list of signal cards. Each card shows:
- Colored left border (green=BUY, red=SELL)
- Pair + timeframe + strategy badge
- Entry / SL / TP1 prices in monospace
- Confidence bar (CSS progress bar, color: green>75, amber 60-75)
- Timestamp (relative: "2 hours ago")
- Status badge: EXECUTED (green) / SIGNAL_ONLY (blue) / REJECTED (grey)

─── SECTION 2: Signals ───

Full signal history table. Fetch GET /signals with pagination (page_size=20).
Implement:
- Filter bar: dropdown for Pair (All + each instrument), Direction (All/BUY/SELL),
  Strategy (All/SCALPING/SWING), Status (All/EXECUTED/SIGNAL_ONLY/REJECTED)
- Sortable columns: Timestamp, Pair, Confidence, R:R
- Pagination controls (Previous / Page N of M / Next)
- Each row: Timestamp | Pair | Direction | Order Type | Entry | SL | TP1 | R:R | Confidence | Status
- Click a row → expand inline to show full "reasoning" text from Claude

─── SECTION 3: Trades ───

Executed trades table. Fetch GET /trades with pagination.
Columns: Timestamp | Pair | Direction | Lot Size | Entry | SL | TP1 | Status | Profit/Loss
- Color profit/loss column green/red
- Status badges: PENDING (amber), FILLED (blue), CLOSED_WIN (green), CLOSED_LOSS (red),
  CANCELLED (grey)
- Summary row at bottom: Total Trades | Win | Loss | Total P&L

─── SECTION 4: Performance ───

Period selector tabs: Today | This Week | This Month | All Time
Fetch GET /performance?period={selected} on tab change.

Display:
- 4 metric cards: Total Signals | Executed | Win Rate | Net P&L
- Equity curve chart (Chart.js line) — cumulative P&L over selected period
- Win/Loss doughnut chart (Chart.js) — wins vs losses
- Best/Worst trade callout cards
- Per-instrument breakdown table: Pair | Trades | Win% | Net P&L | Avg R:R

─── SECTION 5: Settings ───

Display-only settings panel (not editable from dashboard — editing requires .env changes):
- Current bot configuration from GET /status (mode, risk%, max positions, etc.)
- Bot Control buttons:
  - "⏸ Pause Bot" → POST /pause, update header status pill
  - "▶ Resume Bot" → POST /resume, update header status pill
  - "⚡ Manual Scan" → opens a modal with pair/timeframe/strategy dropdowns,
    on confirm → POST /execute with selected params, show result in a toast notification
  - "Switch to SIGNAL_ONLY" / "Switch to AUTO_EXECUTE" — show a confirmation modal
    before toggling (important: this calls POST /mode if you add that endpoint, or shows
    a reminder to change .env and restart)

Toast notifications:
- Implement a toast system (bottom-right, auto-dismiss after 4s) for all POST actions
- Success toast: green background, checkmark icon
- Error toast: red background, warning icon

All API errors must show a toast with the error message, never crash silently.
```

---

## PROMPT 3 of 4 — FastAPI WebSocket + Real-time Updates

```
Read MASTER_CONTEXT.md sections 13 (API Endpoints) and the FastAPI setup in api/main.py
and api/routes.py before writing any code.

Add real-time WebSocket support so the dashboard updates instantly when new signals arrive,
rather than waiting for the 30-second poll.

─── FILE 1: api/routes.py — add WebSocket endpoint ───

Add to the existing FastAPI router:

`WS /ws/live` — WebSocket endpoint that:
- Accepts connections from dashboard clients
- Maintains a connection registry (set of active WebSocket connections)
- Broadcasts a JSON message to ALL connected clients whenever:
  1. A new signal is generated (event: "new_signal", data: signal.model_dump())
  2. An order is executed (event: "order_executed", data: execution result)
  3. Bot state changes (event: "bot_state_change", data: {"state": "PAUSED/RUNNING"})
  4. Daily loss limit triggered (event: "daily_loss_limit", data: {"loss_pct": X})

Implement:
- `ConnectionManager` class with: connect(), disconnect(), broadcast(message: dict)
- Store active connections in a `set[WebSocket]`
- Handle WebSocket disconnect gracefully (remove from set)
- JSON message format: {"event": "event_name", "data": {...}, "timestamp": "ISO string"}

Export the `ConnectionManager` instance as `ws_manager` so it can be imported and called
from `core/signal_engine.py` to broadcast events.

─── FILE 2: core/signal_engine.py — call ws_manager.broadcast() ───

Update `process_pair()` method to call `await ws_manager.broadcast(...)` after:
1. A valid TradeSignal is generated (before execution)
2. An order is successfully placed
3. A signal is rejected (with reason)

Import `ws_manager` from `api.routes` — use TYPE_CHECKING to avoid circular imports
if necessary, or pass the broadcast callback as a dependency injection to SignalEngine.

─── FILE 3: dashboard/index.html — add WebSocket client ───

Add to the existing JavaScript:
- Connect to `ws://localhost:8000/ws/live` on page load
- Reconnect automatically after 3 seconds if connection drops (use exponential backoff:
  3s → 6s → 12s → max 30s)
- Show connection status in header: green dot (connected) / grey dot (disconnected)
- On receiving "new_signal" event:
  1. Prepend the new signal card to the Recent Signals feed (with a brief flash animation)
  2. Show a toast: "🔔 New Signal: {pair} {direction} (Confidence: {confidence}%)"
  3. Update the signals count in the stat card
- On receiving "order_executed":
  1. Show a toast: "✅ Order Placed: #{order_id} — {pair} {direction}"
  2. Refresh the open positions table
- On receiving "bot_state_change":
  1. Update the header status pill immediately
  2. Show a toast with the new state
- On receiving "daily_loss_limit":
  1. Show a persistent red banner at the top: "⚠️ DAILY LOSS LIMIT REACHED — Bot Paused"
  2. Update header status pill to PAUSED/red

Keep the existing 30-second polling as a fallback for data that WebSocket doesn't cover
(performance stats, trade history). Polling should NOT run for data already handled by WS.
```

---

## PROMPT 4 of 4 — Dashboard Static Serving + Screenshot Feature

```
Read MASTER_CONTEXT.md sections 4 (File Structure) and 13 (API Endpoints) before writing any code.

Implement two final features: serve the dashboard from FastAPI, and add chart screenshot
logging to the signal history.

─── FILE 1: api/main.py — serve dashboard as static files ───

Update the FastAPI app to serve the dashboard:
- Mount `dashboard/` as StaticFiles at path `/dashboard`
- Add a redirect route: GET `/` → redirects to `/dashboard/index.html`
- This means the dashboard is accessible at http://localhost:8000 in any browser
- No additional server needed — FastAPI serves everything

─── FILE 2: api/routes.py — add screenshot endpoint ───

Add: POST `/signals/{signal_id}/screenshot`
- Accepts a multipart form upload of an image file (PNG/JPG, max 5MB)
- Validates file type by checking magic bytes (first 4 bytes), NOT just file extension
  (per security rules: validate by file signature)
- Saves the file to `dashboard/static/screenshots/{signal_id}.png`
  (create the directory if it doesn't exist)
- Updates the trade_signals record in the database: set has_screenshot=True
- Returns: {"success": true, "path": "/dashboard/static/screenshots/{signal_id}.png"}
- Returns 400 if file type is invalid, 413 if file too large, 404 if signal_id not found

Add `has_screenshot` boolean column to the `TradeSignalRecord` model and migration.

─── FILE 3: dashboard/index.html — screenshot upload in signal cards ───

In the Signals section, update signal row/card expand behavior:
- When a row is expanded, if has_screenshot is true: show the screenshot image inline
  below the reasoning text (max-width: 100%, border-radius: 4px)
- If has_screenshot is false: show a "📸 Add Screenshot" button
- Clicking the button opens a file picker (accept="image/png,image/jpeg")
- On file select: upload via fetch() to POST /signals/{signal_id}/screenshot as FormData
- Show upload progress (simple spinner)
- On success: replace the button with the uploaded image (no page reload)
- On error: show error toast

─── FILE 4: Update MASTER_CONTEXT.md ───

Update Section 19 (Roadmap) to check off all Phase 2 items:
- [x] Real-time web dashboard (FastAPI + HTML/JS)
- [x] Live P&L display
- [x] Signal history with filtering
- [x] Manual trade execution from dashboard
- [x] Bot state control (pause/resume/switch mode)
- [x] Chart screenshots in signal log

Add a new Section 20: "Dashboard" that documents:
- How to access: http://localhost:8000
- The 5 sections and what each shows
- WebSocket events reference table
- Screenshot upload specs
```
