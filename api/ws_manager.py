"""
api/ws_manager.py
=================
WebSocket connection manager — broadcasts real-time events
to all connected dashboard clients.

Import the singleton `ws_manager` in routes.py and signal_engine.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import WebSocket
from loguru import logger


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts JSON events."""

    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WS client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected WebSocket."""
        self.active_connections.discard(websocket)
        logger.info(f"WS client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, event: str, data: dict) -> None:
        """Send a JSON message to all connected clients.

        Message format:
            {"event": "<name>", "data": {...}, "timestamp": "<ISO>"}

        Stale connections are silently removed.
        """
        message = json.dumps({
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        dead: set[WebSocket] = set()
        for ws in list(self.active_connections):
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.active_connections.discard(ws)


# Singleton — import this everywhere
ws_manager = ConnectionManager()
