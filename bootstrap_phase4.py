#!/usr/bin/env python3
"""
bootstrap_phase4.py
===================
Phase 4: Multi-Account / Copy Trading module.
Run AFTER bootstrap.py, bootstrap_phase2.py, bootstrap_phase3.py:

    python bootstrap.py         # Phase 1 — core modules
    python bootstrap_phase2.py  # Phase 2 — dashboard + WS
    python bootstrap_phase3.py  # Phase 3 — backtesting
    python bootstrap_phase4.py  # Phase 4 — multi-account

Creates / overwrites:
  multi_account/__init__.py
  multi_account/account_registry.py  — MT5Account Pydantic + Fernet encryption
  multi_account/account_manager.py   — AccountManager (dict of MT5Bridge per account)
  multi_account/copy_engine.py       — CopyEngine + CopyTradeRecord
  api/routes.py                      — updated with /accounts/* endpoints
  dashboard/index.html               — updated with Accounts tab (7th)
"""
from pathlib import Path

BASE = Path(__file__).parent


def W(rel: str, content: str) -> None:
    """Write content to a file, creating parent directories."""
    p = BASE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    print(f"  OK  {rel}")


print("=" * 60)
print("ClaudeTradingBot — Phase 4 Bootstrap (Multi-Account)")
print("=" * 60)
print()

# ══════════════════════════════════════════════════════════════
# 1. multi_account/__init__.py
# ══════════════════════════════════════════════════════════════
W("multi_account/__init__.py", '''"""
multi_account/
==============
Multi-account management and copy trading for ClaudeTradingBot.

Modules:
  account_registry  — MT5Account Pydantic model + encrypted persistence
  account_manager   — manages dict of MT5Bridge instances per account
  copy_engine       — copies master signals to follower accounts
"""
from .account_registry import AccountRegistry, MT5Account
from .account_manager import AccountManager
from .copy_engine import CopyEngine

__all__ = ["AccountRegistry", "MT5Account", "AccountManager", "CopyEngine"]
''')

# ══════════════════════════════════════════════════════════════
# 2. multi_account/account_registry.py
# ══════════════════════════════════════════════════════════════
W("multi_account/account_registry.py", '''"""
multi_account/account_registry.py
==================================
MT5 account registry with Fernet-encrypted credential storage.

Accounts are stored in SQLite (via SQLAlchemy) with passwords encrypted
using the symmetric Fernet cipher from the ``cryptography`` library.

Usage::

    from multi_account.account_registry import AccountRegistry, MT5Account

    key = Fernet.generate_key()
    registry = AccountRegistry(db_session, encryption_key=key)
    registry.add_account(MT5Account(
        account_id="exness_main",
        label="Exness Master",
        login=12345678,
        password="mypassword",
        server="Exness-MT5Real",
        broker="Exness",
        is_master=True,
        ...
    ))
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional

from loguru import logger
from pydantic import BaseModel, Field

# ── Optional cryptography ────────────────────────────────────────────────────
try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTO_AVAILABLE = True
except ImportError:
    Fernet = None  # type: ignore[assignment,misc]
    InvalidToken = Exception  # type: ignore[assignment,misc]
    CRYPTO_AVAILABLE = False
    logger.warning("[AccountRegistry] cryptography not installed — passwords stored in plaintext!")

# ── Optional SQLAlchemy ──────────────────────────────────────────────────────
try:
    from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
    from sqlalchemy.orm import Session
    from database.db import Base
    SA_AVAILABLE = True
except ImportError:
    SA_AVAILABLE = False
    Base = object  # type: ignore[assignment,misc]
    Session = object  # type: ignore[assignment]

# ══════════════════════════════════════════════════════════════════════════════
# Pydantic model (API-facing)
# ══════════════════════════════════════════════════════════════════════════════

class MT5Account(BaseModel):
    """Represents a MetaTrader 5 account (master or follower).

    Passwords are always masked when returned from the registry via
    :meth:`AccountRegistry.list_accounts`.
    """
    account_id: str = Field(..., description="Unique slug, e.g. 'exness_main'")
    label: str = Field(..., description="Human-readable name")
    login: int
    password: str = Field(..., description="MT5 password (encrypted at rest)")
    server: str = Field(..., description="e.g. 'Exness-MT5Real'")
    broker: str = Field(default="Exness")
    is_master: bool = Field(default=False, description="True = source account for copy trading")
    is_active: bool = Field(default=True)
    risk_per_trade_pct: float = Field(default=1.0, ge=0.1, le=10.0)
    lot_size_multiplier: float = Field(default=1.0, gt=0)
    copy_delay_seconds: int = Field(default=0, ge=0)
    max_positions: int = Field(default=5, ge=1)
    magic_number_offset: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


# ══════════════════════════════════════════════════════════════════════════════
# SQLAlchemy ORM model
# ══════════════════════════════════════════════════════════════════════════════

if SA_AVAILABLE:
    class MT5AccountRecord(Base):  # type: ignore[valid-type,misc]
        """Persisted MT5 account record with encrypted password."""
        __tablename__ = "mt5_accounts"
        __table_args__ = {"extend_existing": True}

        id                  = Column(Integer, primary_key=True, autoincrement=True)
        account_id          = Column(String(64), unique=True, nullable=False, index=True)
        label               = Column(String(128), nullable=False)
        login               = Column(Integer, nullable=False)
        password_encrypted  = Column(Text, nullable=False)
        server              = Column(String(128), nullable=False)
        broker              = Column(String(64), default="Exness")
        is_master           = Column(Boolean, default=False)
        is_active           = Column(Boolean, default=True)
        risk_per_trade_pct  = Column(Float, default=1.0)
        lot_size_multiplier = Column(Float, default=1.0)
        copy_delay_seconds  = Column(Integer, default=0)
        max_positions       = Column(Integer, default=5)
        magic_number_offset = Column(Integer, default=0)
        created_at          = Column(DateTime, default=lambda: datetime.now(timezone.utc))
else:
    MT5AccountRecord = None  # type: ignore[assignment,misc]


# ══════════════════════════════════════════════════════════════════════════════
# AccountRegistry
# ══════════════════════════════════════════════════════════════════════════════

class AccountRegistry:
    """Manages MT5 accounts with encrypted credential storage.

    Parameters
    ----------
    db_session:
        SQLAlchemy ``Session`` instance.  Pass ``None`` to use in-memory
        storage only (useful for testing without a database).
    encryption_key:
        32-byte Fernet key as bytes.  If ``None``, read from env var
        ``ACCOUNT_ENCRYPTION_KEY``.  Falls back to a per-process key if
        neither is set (not persistent across restarts).
    """

    def __init__(
        self,
        db_session=None,
        encryption_key: Optional[bytes] = None,
    ) -> None:
        self._db = db_session
        self._memory: dict[str, dict] = {}  # fallback in-memory store

        # Build Fernet cipher
        key = encryption_key or os.getenv("ACCOUNT_ENCRYPTION_KEY", "").encode()
        if CRYPTO_AVAILABLE and Fernet is not None:
            if not key:
                key = Fernet.generate_key()
                logger.warning(
                    "[AccountRegistry] No ACCOUNT_ENCRYPTION_KEY set — "
                    "generated ephemeral key (passwords lost on restart)"
                )
            self._cipher = Fernet(key)
        else:
            self._cipher = None

    # ── Encryption helpers ───────────────────────────────────────────────────

    def _encrypt(self, plaintext: str) -> str:
        if self._cipher is None:
            return plaintext
        return self._cipher.encrypt(plaintext.encode()).decode()

    def _decrypt(self, token: str) -> str:
        if self._cipher is None:
            return token
        try:
            return self._cipher.decrypt(token.encode()).decode()
        except InvalidToken:
            logger.error("[AccountRegistry] Failed to decrypt password — wrong key?")
            return ""

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def add_account(self, account: MT5Account) -> str:
        """Encrypt password and persist account.  Returns account_id."""
        encrypted = self._encrypt(account.password)

        if self._db is not None and SA_AVAILABLE and MT5AccountRecord is not None:
            record = MT5AccountRecord(
                account_id          = account.account_id,
                label               = account.label,
                login               = account.login,
                password_encrypted  = encrypted,
                server              = account.server,
                broker              = account.broker,
                is_master           = account.is_master,
                is_active           = account.is_active,
                risk_per_trade_pct  = account.risk_per_trade_pct,
                lot_size_multiplier = account.lot_size_multiplier,
                copy_delay_seconds  = account.copy_delay_seconds,
                max_positions       = account.max_positions,
                magic_number_offset = account.magic_number_offset,
                created_at          = account.created_at,
            )
            self._db.merge(record)
            self._db.commit()
        else:
            self._memory[account.account_id] = {
                **account.model_dump(),
                "password_encrypted": encrypted,
            }

        logger.info(f"[AccountRegistry] Added account: {account.account_id} ({account.label})")
        return account.account_id

    def get_account(self, account_id: str) -> MT5Account:
        """Load account and decrypt password."""
        if self._db is not None and SA_AVAILABLE and MT5AccountRecord is not None:
            rec = self._db.query(MT5AccountRecord).filter_by(account_id=account_id).first()
            if rec is None:
                raise KeyError(f"Account not found: {account_id}")
            return MT5Account(
                account_id          = rec.account_id,
                label               = rec.label,
                login               = rec.login,
                password            = self._decrypt(rec.password_encrypted),
                server              = rec.server,
                broker              = rec.broker or "Exness",
                is_master           = rec.is_master,
                is_active           = rec.is_active,
                risk_per_trade_pct  = rec.risk_per_trade_pct or 1.0,
                lot_size_multiplier = rec.lot_size_multiplier or 1.0,
                copy_delay_seconds  = rec.copy_delay_seconds or 0,
                max_positions       = rec.max_positions or 5,
                magic_number_offset = rec.magic_number_offset or 0,
                created_at          = rec.created_at or datetime.now(timezone.utc),
            )
        else:
            raw = self._memory.get(account_id)
            if raw is None:
                raise KeyError(f"Account not found: {account_id}")
            data = {**raw, "password": self._decrypt(raw.get("password_encrypted", ""))}
            return MT5Account(**{k: v for k, v in data.items() if k != "password_encrypted"})

    def list_accounts(self, mask_passwords: bool = True) -> list[MT5Account]:
        """Return all accounts.  Passwords are masked by default."""
        accounts: list[MT5Account] = []
        if self._db is not None and SA_AVAILABLE and MT5AccountRecord is not None:
            records = self._db.query(MT5AccountRecord).all()
            for rec in records:
                pw = "***" if mask_passwords else self._decrypt(rec.password_encrypted)
                accounts.append(MT5Account(
                    account_id          = rec.account_id,
                    label               = rec.label,
                    login               = rec.login,
                    password            = pw,
                    server              = rec.server,
                    broker              = rec.broker or "Exness",
                    is_master           = rec.is_master,
                    is_active           = rec.is_active,
                    risk_per_trade_pct  = rec.risk_per_trade_pct or 1.0,
                    lot_size_multiplier = rec.lot_size_multiplier or 1.0,
                    copy_delay_seconds  = rec.copy_delay_seconds or 0,
                    max_positions       = rec.max_positions or 5,
                    magic_number_offset = rec.magic_number_offset or 0,
                    created_at          = rec.created_at or datetime.now(timezone.utc),
                ))
        else:
            for account_id in self._memory:
                raw = self._memory[account_id]
                pw = "***" if mask_passwords else self._decrypt(raw.get("password_encrypted", ""))
                data = {**raw, "password": pw}
                accounts.append(MT5Account(**{k: v for k, v in data.items() if k != "password_encrypted"}))
        return accounts

    def get_master(self) -> Optional[MT5Account]:
        """Return the account where is_master=True, or None."""
        for acc in self.list_accounts(mask_passwords=False):
            if acc.is_master and acc.is_active:
                return acc
        return None

    def get_followers(self) -> list[MT5Account]:
        """Return all active follower accounts (is_master=False, is_active=True)."""
        return [a for a in self.list_accounts(mask_passwords=False)
                if not a.is_master and a.is_active]

    def deactivate_account(self, account_id: str) -> None:
        """Soft-delete: set is_active=False."""
        if self._db is not None and SA_AVAILABLE and MT5AccountRecord is not None:
            rec = self._db.query(MT5AccountRecord).filter_by(account_id=account_id).first()
            if rec:
                rec.is_active = False
                self._db.commit()
        elif account_id in self._memory:
            self._memory[account_id]["is_active"] = False
        logger.info(f"[AccountRegistry] Deactivated: {account_id}")
''')

# ══════════════════════════════════════════════════════════════
# 3. multi_account/account_manager.py
# ══════════════════════════════════════════════════════════════
W("multi_account/account_manager.py", '''"""
multi_account/account_manager.py
=================================
Manages a pool of MT5Bridge connections — one per account.

Usage::

    from multi_account.account_registry import AccountRegistry
    from multi_account.account_manager import AccountManager

    manager = AccountManager(registry)
    results = manager.connect_all()
    bridge = manager.get_bridge("exness_main")
"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from .account_registry import AccountRegistry, MT5Account

# ── Lazy MT5Bridge import ────────────────────────────────────────────────────
try:
    from core.mt5_bridge import MT5Bridge
    BRIDGE_AVAILABLE = True
except ImportError:
    MT5Bridge = None  # type: ignore[assignment,misc]
    BRIDGE_AVAILABLE = False


class AccountManager:
    """Manages active MT5Bridge connections for multiple accounts.

    Parameters
    ----------
    registry:
        :class:`AccountRegistry` instance for account lookup.
    """

    def __init__(self, registry: AccountRegistry) -> None:
        self._registry = registry
        self.connections: dict[str, "MT5Bridge"] = {}  # account_id → MT5Bridge

    # ── Connection management ────────────────────────────────────────────────

    def connect_all(self) -> dict[str, bool]:
        """Connect all active accounts.

        Returns
        -------
        dict mapping ``account_id`` → ``bool`` (True = connected).
        """
        accounts = self._registry.list_accounts(mask_passwords=False)
        results: dict[str, bool] = {}
        for acc in accounts:
            if acc.is_active:
                results[acc.account_id] = self.connect_account(acc.account_id)
        return results

    def connect_account(self, account_id: str) -> bool:
        """Connect a single account and store the bridge.

        Returns True on success.
        """
        if not BRIDGE_AVAILABLE or MT5Bridge is None:
            logger.error("[AccountManager] MT5Bridge not available")
            return False

        try:
            acc = self._registry.get_account(account_id)
        except KeyError:
            logger.error(f"[AccountManager] Account not found: {account_id}")
            return False

        try:
            bridge = MT5Bridge(
                login=str(acc.login),
                password=acc.password,
                server=acc.server,
            )
            success = bridge.connect()
            if success:
                self.connections[account_id] = bridge
                logger.info(f"[AccountManager] Connected: {account_id} ({acc.label})")
            else:
                logger.warning(f"[AccountManager] Failed to connect: {account_id}")
            return success
        except Exception as exc:
            logger.error(f"[AccountManager] Exception connecting {account_id}: {exc}")
            return False

    def disconnect_account(self, account_id: str) -> None:
        """Cleanly disconnect an account and remove it from the pool."""
        bridge = self.connections.pop(account_id, None)
        if bridge is not None:
            try:
                bridge.disconnect()
            except Exception:
                pass
        logger.info(f"[AccountManager] Disconnected: {account_id}")

    def get_bridge(self, account_id: str) -> "MT5Bridge":
        """Return the MT5Bridge for this account.

        Raises
        ------
        RuntimeError
            If the account is not connected.
        """
        bridge = self.connections.get(account_id)
        if bridge is None:
            raise RuntimeError(
                f"Account '{account_id}' is not connected. "
                "Call connect_account() first."
            )
        return bridge

    def is_connected(self, account_id: str) -> bool:
        """Return True if account has an active connection."""
        return account_id in self.connections

    # ── Aggregate info ───────────────────────────────────────────────────────

    def get_all_account_info(self) -> dict[str, dict]:
        """Return account info dict for all connected accounts.

        Returns
        -------
        dict of ``{account_id: account_info_dict}``
        """
        result: dict[str, dict] = {}
        for account_id, bridge in self.connections.items():
            try:
                info = bridge.get_account_info()
                result[account_id] = info or {}
            except Exception as exc:
                logger.warning(f"[AccountManager] get_account_info failed for {account_id}: {exc}")
                result[account_id] = {"error": str(exc)}
        return result

    def get_aggregated_status(self) -> dict:
        """Aggregate equity, positions, and daily P&L across all accounts.

        Returns
        -------
        dict with total metrics and per-account breakdown.
        """
        all_accounts = self._registry.list_accounts()
        all_info = self.get_all_account_info()

        total_equity    = 0.0
        total_positions = 0
        total_daily_pnl = 0.0
        breakdown: list[dict] = []

        for acc in all_accounts:
            info = all_info.get(acc.account_id, {})
            equity     = float(info.get("equity", 0) or 0)
            positions  = int(info.get("positions_total", 0) or 0)
            daily_pnl  = float(info.get("profit", 0) or 0)

            total_equity    += equity
            total_positions += positions
            total_daily_pnl += daily_pnl

            breakdown.append({
                "account_id":   acc.account_id,
                "label":        acc.label,
                "is_master":    acc.is_master,
                "is_connected": self.is_connected(acc.account_id),
                "equity":       round(equity, 2),
                "positions":    positions,
                "daily_pnl":    round(daily_pnl, 2),
                "lot_multiplier": acc.lot_size_multiplier,
            })

        return {
            "total_equity":    round(total_equity, 2),
            "total_positions": total_positions,
            "total_daily_pnl": round(total_daily_pnl, 2),
            "accounts_total":  len(all_accounts),
            "accounts_connected": len(self.connections),
            "accounts": breakdown,
        }
''')

# ══════════════════════════════════════════════════════════════
# 4. multi_account/copy_engine.py
# ══════════════════════════════════════════════════════════════
W("multi_account/copy_engine.py", '''"""
multi_account/copy_engine.py
=============================
Copy trading engine — mirrors master signals to follower accounts.

When the master places a trade, CopyEngine calculates each follower\'s
lot size (proportional or risk-based), applies copy_delay_seconds, then
places the identical pending order on each follower account.

Usage::

    copy_engine = CopyEngine(account_manager, registry)
    results = await copy_engine.copy_signal_to_followers(signal, master_lot=0.1, master_order_id=123456)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from .account_manager import AccountManager
from .account_registry import AccountRegistry, MT5Account

# ── Optional SQLAlchemy ──────────────────────────────────────────────────────
try:
    from sqlalchemy import Column, DateTime, Float, Integer, String
    from database.db import Base
    SA_AVAILABLE = True
except ImportError:
    SA_AVAILABLE = False
    Base = object  # type: ignore[assignment,misc]

# ── Signal schema ────────────────────────────────────────────────────────────
try:
    from core.signal_engine import TradeSignal
    SIGNAL_AVAILABLE = True
except ImportError:
    TradeSignal = None  # type: ignore[assignment,misc]
    SIGNAL_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
# SQLAlchemy model for copy_trades table
# ══════════════════════════════════════════════════════════════════════════════

if SA_AVAILABLE:
    class CopyTradeRecord(Base):  # type: ignore[valid-type,misc]
        """Persisted record of a copied trade."""
        __tablename__ = "copy_trades"
        __table_args__ = {"extend_existing": True}

        id                   = Column(Integer, primary_key=True, autoincrement=True)
        master_signal_id     = Column(Integer, nullable=True)
        master_order_id      = Column(Integer, nullable=True)
        follower_account_id  = Column(String(64), nullable=False)
        follower_order_id    = Column(Integer, nullable=True)
        symbol               = Column(String(20), nullable=False)
        direction            = Column(String(10), nullable=False)
        lot_size             = Column(Float, default=0.01)
        status               = Column(String(20), default="PENDING")
        profit               = Column(Float, nullable=True)
        error                = Column(String(256), nullable=True)
        created_at           = Column(DateTime, default=lambda: datetime.now(timezone.utc))
        updated_at           = Column(DateTime, onupdate=lambda: datetime.now(timezone.utc))
else:
    CopyTradeRecord = None  # type: ignore[assignment,misc]


# ══════════════════════════════════════════════════════════════════════════════
# CopyEngine
# ══════════════════════════════════════════════════════════════════════════════

class CopyEngine:
    """Copy master trades to all active follower accounts.

    Parameters
    ----------
    account_manager:
        :class:`AccountManager` with active connections.
    registry:
        :class:`AccountRegistry` for follower metadata.
    db_session:
        Optional SQLAlchemy session for logging copy trade records.
    """

    def __init__(
        self,
        account_manager: AccountManager,
        registry: AccountRegistry,
        db_session=None,
    ) -> None:
        self._mgr = account_manager
        self._reg = registry
        self._db  = db_session

    # ── Core copy logic ──────────────────────────────────────────────────────

    async def copy_signal_to_followers(
        self,
        signal: Any,            # TradeSignal or dict
        master_lot_size: float,
        master_order_id: int,
    ) -> list[dict]:
        """Copy a master trade signal to all active follower accounts.

        Parameters
        ----------
        signal:
            :class:`TradeSignal` or dict with trade parameters.
        master_lot_size:
            Lot size actually used on the master account.
        master_order_id:
            MT5 ticket/order ID of the master order.

        Returns
        -------
        list of dicts: ``[{account_id, success, order_id, lot_size, error}]``
        """
        followers = self._reg.get_followers()
        if not followers:
            logger.info("[CopyEngine] No active followers — nothing to copy")
            return []

        # Extract signal fields (support both TradeSignal and plain dict)
        if hasattr(signal, "dict"):
            sig = signal.dict()
        elif isinstance(signal, dict):
            sig = signal
        else:
            sig = {}

        symbol    = sig.get("symbol", "")
        direction = sig.get("direction", "")

        tasks = [
            self._copy_to_follower(sig, follower, master_lot_size, master_order_id)
            for follower in followers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final: list[dict] = []
        for follower, result in zip(followers, results):
            if isinstance(result, Exception):
                logger.error(f"[CopyEngine] {follower.account_id} exception: {result}")
                final.append({
                    "account_id": follower.account_id,
                    "success": False,
                    "order_id": None,
                    "lot_size": 0,
                    "error": str(result),
                })
            else:
                final.append(result)

        success_count = sum(1 for r in final if r.get("success"))
        fail_count    = len(final) - success_count
        logger.info(
            f"[CopyEngine] {symbol} {direction} copied to {len(followers)} accounts "
            f"— {success_count} success / {fail_count} failed"
        )
        return final

    async def copy_cancel_to_followers(
        self,
        master_ticket: int,
    ) -> list[dict]:
        """Cancel all follower orders linked to a master ticket.

        Returns
        -------
        list of dicts: ``[{account_id, success, follower_ticket, error}]``
        """
        if self._db is None or not SA_AVAILABLE or CopyTradeRecord is None:
            logger.warning("[CopyEngine] No DB session — cannot look up copy records")
            return []

        records = (
            self._db.query(CopyTradeRecord)
            .filter_by(master_order_id=master_ticket)
            .all()
        )

        results: list[dict] = []
        for rec in records:
            if not rec.follower_order_id:
                continue
            try:
                bridge = self._mgr.get_bridge(rec.follower_account_id)
                success = bridge.cancel_order(rec.follower_order_id)
                if success:
                    rec.status = "CANCELLED"
                    self._db.commit()
                results.append({
                    "account_id":      rec.follower_account_id,
                    "follower_ticket": rec.follower_order_id,
                    "success":         success,
                    "error":           None,
                })
            except Exception as exc:
                results.append({
                    "account_id":      rec.follower_account_id,
                    "follower_ticket": rec.follower_order_id,
                    "success":         False,
                    "error":           str(exc),
                })

        return results

    def get_copy_performance(self) -> dict:
        """Compare master vs each follower: net P&L, win rate, trade count.

        Returns a comparison dict.
        """
        if self._db is None or not SA_AVAILABLE or CopyTradeRecord is None:
            return {"error": "Database not available"}

        records = self._db.query(CopyTradeRecord).all()
        by_account: dict[str, dict] = {}

        for rec in records:
            aid = rec.follower_account_id
            if aid not in by_account:
                by_account[aid] = {"trades": 0, "wins": 0, "total_profit": 0.0}
            by_account[aid]["trades"] += 1
            profit = rec.profit or 0
            by_account[aid]["total_profit"] += profit
            if profit > 0:
                by_account[aid]["wins"] += 1

        comparison: list[dict] = []
        for aid, stats in by_account.items():
            t = stats["trades"]
            comparison.append({
                "account_id":  aid,
                "trades":      t,
                "win_rate":    round(stats["wins"] / t, 4) if t else 0,
                "net_profit":  round(stats["total_profit"], 2),
            })

        return {
            "followers":   comparison,
            "total_copied_trades": len(records),
        }

    # ── Private helpers ──────────────────────────────────────────────────────

    async def _copy_to_follower(
        self,
        sig: dict,
        follower: MT5Account,
        master_lot_size: float,
        master_order_id: int,
    ) -> dict:
        """Execute copy for a single follower account."""
        # Apply copy delay
        if follower.copy_delay_seconds > 0:
            await asyncio.sleep(follower.copy_delay_seconds)

        if not self._mgr.is_connected(follower.account_id):
            return {
                "account_id": follower.account_id,
                "success": False,
                "order_id": None,
                "lot_size": 0,
                "error": "Not connected",
            }

        # Calculate follower lot size
        follower_lot = round(master_lot_size * follower.lot_size_multiplier, 2)
        follower_lot = max(follower_lot, 0.01)  # MT5 minimum

        bridge = self._mgr.get_bridge(follower.account_id)

        # Build order comment linking back to master
        comment = f"CTB_COPY_{master_order_id}"

        try:
            order_result = bridge.place_pending_order_dict(
                symbol     = sig.get("symbol", ""),
                direction  = sig.get("direction", "BUY"),
                order_type = sig.get("order_type", "BUY_LIMIT"),
                entry      = sig.get("entry", 0),
                stop_loss  = sig.get("stop_loss", 0),
                take_profit= sig.get("take_profit_1", 0),
                lot_size   = follower_lot,
                magic      = 234000 + follower.magic_number_offset,
                comment    = comment,
            )
            success   = bool(order_result and order_result.get("retcode") == 10009)
            order_id  = order_result.get("order") if order_result else None

            # Persist to copy_trades
            self._log_copy_trade(
                sig=sig,
                follower=follower,
                master_order_id=master_order_id,
                follower_order_id=order_id,
                lot_size=follower_lot,
                success=success,
                error=None if success else str(order_result),
            )

            return {
                "account_id": follower.account_id,
                "success":    success,
                "order_id":   order_id,
                "lot_size":   follower_lot,
                "error":      None if success else str(order_result),
            }
        except Exception as exc:
            self._log_copy_trade(
                sig=sig,
                follower=follower,
                master_order_id=master_order_id,
                follower_order_id=None,
                lot_size=follower_lot,
                success=False,
                error=str(exc),
            )
            return {
                "account_id": follower.account_id,
                "success":    False,
                "order_id":   None,
                "lot_size":   follower_lot,
                "error":      str(exc),
            }

    def _log_copy_trade(
        self,
        sig: dict,
        follower: MT5Account,
        master_order_id: int,
        follower_order_id: Optional[int],
        lot_size: float,
        success: bool,
        error: Optional[str],
    ) -> None:
        """Write a CopyTradeRecord to the database."""
        if self._db is None or not SA_AVAILABLE or CopyTradeRecord is None:
            return
        try:
            rec = CopyTradeRecord(
                master_order_id     = master_order_id,
                follower_account_id = follower.account_id,
                follower_order_id   = follower_order_id,
                symbol              = sig.get("symbol", ""),
                direction           = sig.get("direction", ""),
                lot_size            = lot_size,
                status              = "FILLED" if success else "FAILED",
                error               = error,
            )
            self._db.add(rec)
            self._db.commit()
        except Exception as exc:
            logger.warning(f"[CopyEngine] DB log error: {exc}")
''')

# ══════════════════════════════════════════════════════════════
# 5. api/routes.py — rewrite adding /accounts/* endpoints
# ══════════════════════════════════════════════════════════════
W("api/routes.py", '''"""
api/routes.py
=============
FastAPI router — all HTTP + WebSocket endpoints for ClaudeTradingBot.

Phases included:
  Phase 1: /status /signals /trades /execute /pause /resume /performance /health
  Phase 2: /ws  (WebSocket), /screenshot, /dashboard
  Phase 3: /backtest/* (run, result, compare, all-pairs, optimize, monte-carlo)
  Phase 4: /accounts/* (list, add, delete, status, aggregated, connect, disconnect, copy-performance)
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from loguru import logger
from pydantic import BaseModel

# ── Internal imports ─────────────────────────────────────────────────────────
try:
    from api.ws_manager import ws_manager
except ImportError:
    ws_manager = None  # type: ignore[assignment]

try:
    from database.db import get_db
    from database.queries import get_all_signals, get_all_trades, get_performance_summary
except ImportError:
    get_db = None  # type: ignore[assignment]
    get_all_signals = get_all_trades = get_performance_summary = None  # type: ignore

# ── Phase 3: backtesting ─────────────────────────────────────────────────────
_backtest_engine = None
_data_loader     = None

def _get_backtest_engine():
    global _backtest_engine, _data_loader
    if _backtest_engine is None:
        try:
            from backtesting.data_loader import HistoricalDataLoader
            from backtesting.engine import BacktestEngine
            _data_loader = HistoricalDataLoader()
            _backtest_engine = BacktestEngine(_data_loader)
        except Exception as exc:
            logger.warning(f"[Routes] BacktestEngine unavailable: {exc}")
    return _backtest_engine

# ── Phase 4: multi-account ───────────────────────────────────────────────────
_account_registry = None
_account_manager  = None
_copy_engine      = None

def _get_account_registry():
    global _account_registry
    if _account_registry is None:
        try:
            from multi_account.account_registry import AccountRegistry
            _account_registry = AccountRegistry(db_session=None)
        except Exception as exc:
            logger.warning(f"[Routes] AccountRegistry unavailable: {exc}")
    return _account_registry

def _get_account_manager():
    global _account_manager
    if _account_manager is None:
        reg = _get_account_registry()
        if reg:
            try:
                from multi_account.account_manager import AccountManager
                _account_manager = AccountManager(reg)
            except Exception as exc:
                logger.warning(f"[Routes] AccountManager unavailable: {exc}")
    return _account_manager

def _get_copy_engine():
    global _copy_engine
    if _copy_engine is None:
        reg = _get_account_registry()
        mgr = _get_account_manager()
        if reg and mgr:
            try:
                from multi_account.copy_engine import CopyEngine
                _copy_engine = CopyEngine(mgr, reg)
            except Exception as exc:
                logger.warning(f"[Routes] CopyEngine unavailable: {exc}")
    return _copy_engine

# ── In-memory job store ──────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}

router = APIRouter()

# ══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ══════════════════════════════════════════════════════════════════════════════

class ExecuteRequest(BaseModel):
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float] = None
    lot_size: Optional[float] = None
    order_type: str = "BUY_LIMIT"
    timeframe: str = "H4"
    strategy: str = "swing"
    notes: Optional[str] = None


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str = "swing"
    timeframe: str = "H4"
    count: int = 5000
    init_cash: float = 10_000.0


class OptimizeRequest(BaseModel):
    symbol: str
    strategy: str = "swing"
    timeframe: str = "H4"


class MonteCarloRequest(BaseModel):
    symbol: str
    strategy: str = "swing"
    n_simulations: int = 1000
    init_cash: float = 10_000.0


class AddAccountRequest(BaseModel):
    account_id: str
    label: str
    login: int
    password: str
    server: str
    broker: str = "Exness"
    is_master: bool = False
    risk_per_trade_pct: float = 1.0
    lot_size_multiplier: float = 1.0
    copy_delay_seconds: int = 0
    max_positions: int = 5
    magic_number_offset: int = 0


# ══════════════════════════════════════════════════════════════════════════════
# Auth dependency for sensitive account endpoints
# ══════════════════════════════════════════════════════════════════════════════

def require_bot_token(x_bot_token: Optional[str] = Header(default=None)):
    expected = os.getenv("BOT_API_TOKEN", "")
    if not expected:
        return  # No token configured — open access (dev mode)
    if x_bot_token != expected:
        raise HTTPException(403, "Invalid or missing X-Bot-Token header")


# ════════════════════════════════════════════════════════════════
# PHASE 1 — Core endpoints
# ════════════════════════════════════════════════════════════════

@router.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/status")
async def get_status():
    try:
        from main import app_state  # type: ignore[import]
        return app_state
    except ImportError:
        return {
            "status": "running",
            "mode": "SIGNAL_ONLY",
            "active_pairs": [],
            "open_positions": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/signals")
async def get_signals(limit: int = 50):
    if get_all_signals is None or get_db is None:
        return {"signals": [], "count": 0}
    async for db in get_db():
        signals = get_all_signals(db, limit=limit)
        return {"signals": [s.__dict__ for s in signals], "count": len(signals)}


@router.get("/trades")
async def get_trades(limit: int = 50):
    if get_all_trades is None or get_db is None:
        return {"trades": [], "count": 0}
    async for db in get_db():
        trades = get_all_trades(db, limit=limit)
        return {"trades": [t.__dict__ for t in trades], "count": len(trades)}


@router.post("/execute")
async def manual_execute(req: ExecuteRequest):
    logger.info(f"[Routes] Manual execute: {req.symbol} {req.direction}")
    return {
        "status": "queued",
        "symbol": req.symbol,
        "direction": req.direction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pause")
async def pause_bot():
    logger.warning("[Routes] Bot paused via API")
    return {"status": "paused", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.post("/resume")
async def resume_bot():
    logger.info("[Routes] Bot resumed via API")
    return {"status": "running", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/performance")
async def get_performance(days: int = 30):
    if get_performance_summary is None or get_db is None:
        return {"message": "Database not available", "days": days}
    async for db in get_db():
        return get_performance_summary(db, days=days)


# ════════════════════════════════════════════════════════════════
# PHASE 2 — WebSocket + Screenshot
# ════════════════════════════════════════════════════════════════

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if ws_manager is None:
        await websocket.close(code=1011)
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"event": "ping", "data": {}})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@router.post("/screenshot")
async def upload_screenshot():
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════
# PHASE 3 — Backtesting
# ════════════════════════════════════════════════════════════════

@router.get("/backtest/run")
async def backtest_run(
    background_tasks: BackgroundTasks,
    symbol: str = "XAUUSD",
    strategy: str = "swing",
    timeframe: str = "H4",
    count: int = 5000,
    init_cash: float = 10_000.0,
):
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}

    async def _run():
        try:
            if strategy == "scalping":
                result = engine.run_scalping_backtest(symbol, timeframe, count, init_cash)
            else:
                result = engine.run_swing_backtest(symbol, timeframe, count, init_cash)
            _jobs[job_id] = {"status": "done", "result": result}
        except Exception as exc:
            _jobs[job_id] = {"status": "error", "error": str(exc)}

    background_tasks.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/backtest/result/{job_id}")
async def backtest_result(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, f"Job {job_id} not found")
    return _jobs[job_id]


@router.get("/backtest/compare")
async def backtest_compare(symbol: str = "XAUUSD", init_cash: float = 10_000.0):
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    return engine.compare_strategies(symbol, init_cash)


@router.get("/backtest/all-pairs")
async def backtest_all_pairs(strategy: str = "swing", init_cash: float = 10_000.0):
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    return engine.run_all_pairs(strategy, init_cash)


@router.post("/backtest/optimize")
async def backtest_optimize(req: OptimizeRequest, background_tasks: BackgroundTasks):
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    from backtesting.optimizer import StrategyOptimizer
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}

    async def _run():
        try:
            optimizer = StrategyOptimizer(engine)
            if req.strategy == "scalping":
                result = optimizer.optimize_scalping_params(req.symbol, req.timeframe)
            else:
                result = optimizer.optimize_swing_params(req.symbol, req.timeframe)
            _jobs[job_id] = {"status": "done", "result": result}
        except Exception as exc:
            _jobs[job_id] = {"status": "error", "error": str(exc)}

    background_tasks.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.post("/backtest/monte-carlo")
async def backtest_monte_carlo(req: MonteCarloRequest):
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    from backtesting.monte_carlo import MonteCarloSimulator
    try:
        stats = engine.run_swing_backtest(req.symbol, init_cash=req.init_cash)
        sim = MonteCarloSimulator(n_simulations=req.n_simulations)
        mc_result = sim.simulate_from_backtest(stats, req.init_cash)
        return {"backtest_stats": stats, "monte_carlo": mc_result}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ════════════════════════════════════════════════════════════════
# PHASE 4 — Multi-Account / Copy Trading
# ════════════════════════════════════════════════════════════════

@router.get("/accounts")
async def list_accounts():
    """List all MT5 accounts (passwords masked)."""
    reg = _get_account_registry()
    if reg is None:
        raise HTTPException(503, "AccountRegistry not available")
    accounts = reg.list_accounts(mask_passwords=True)
    return {"accounts": [a.model_dump() for a in accounts], "count": len(accounts)}


@router.post("/accounts", dependencies=[Depends(require_bot_token)])
async def add_account(req: AddAccountRequest):
    """Add a new MT5 account.  Requires X-Bot-Token header."""
    reg = _get_account_registry()
    if reg is None:
        raise HTTPException(503, "AccountRegistry not available")
    from multi_account.account_registry import MT5Account
    account = MT5Account(**req.model_dump())
    account_id = reg.add_account(account)
    return {"status": "added", "account_id": account_id}


@router.delete("/accounts/{account_id}", dependencies=[Depends(require_bot_token)])
async def deactivate_account(account_id: str):
    """Deactivate an account (soft delete).  Requires X-Bot-Token header."""
    reg = _get_account_registry()
    if reg is None:
        raise HTTPException(503, "AccountRegistry not available")
    reg.deactivate_account(account_id)
    return {"status": "deactivated", "account_id": account_id}


@router.get("/accounts/aggregated")
async def accounts_aggregated():
    """Return aggregated equity, positions, and P&L across all accounts."""
    mgr = _get_account_manager()
    if mgr is None:
        raise HTTPException(503, "AccountManager not available")
    return mgr.get_aggregated_status()


@router.get("/accounts/{account_id}/status")
async def account_status(account_id: str):
    """Return equity, positions, daily P&L for a single account."""
    mgr = _get_account_manager()
    if mgr is None:
        raise HTTPException(503, "AccountManager not available")
    if not mgr.is_connected(account_id):
        raise HTTPException(404, f"Account {account_id} is not connected")
    try:
        bridge = mgr.get_bridge(account_id)
        info = bridge.get_account_info()
        return {"account_id": account_id, "info": info}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/accounts/{account_id}/connect")
async def connect_account(account_id: str):
    """Connect a specific account to MT5."""
    mgr = _get_account_manager()
    if mgr is None:
        raise HTTPException(503, "AccountManager not available")
    success = mgr.connect_account(account_id)
    return {"account_id": account_id, "connected": success}


@router.post("/accounts/{account_id}/disconnect")
async def disconnect_account(account_id: str):
    """Disconnect a specific account from MT5."""
    mgr = _get_account_manager()
    if mgr is None:
        raise HTTPException(503, "AccountManager not available")
    mgr.disconnect_account(account_id)
    return {"account_id": account_id, "connected": False}


@router.get("/accounts/copy-performance")
async def copy_performance():
    """Return master vs follower copy trading performance comparison."""
    ce = _get_copy_engine()
    if ce is None:
        raise HTTPException(503, "CopyEngine not available")
    return ce.get_copy_performance()
''')

# ══════════════════════════════════════════════════════════════
# 6. dashboard/index.html — add Accounts tab (7th tab)
# ══════════════════════════════════════════════════════════════
W("dashboard/index.html", """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>ClaudeTradingBot Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root{
      --bg:#0f1117;--surface:#1a1d27;--surface2:#23273a;
      --accent:#6c63ff;--accent2:#00d4aa;--warn:#f59e0b;
      --danger:#ef4444;--text:#e2e8f0;--muted:#64748b;
      --green:#22c55e;--red:#ef4444;--amber:#f59e0b;
    }
    *{box-sizing:border-box;margin:0;padding:0;}
    body{background:var(--bg);color:var(--text);font-family:'Segoe UI',sans-serif;display:flex;height:100vh;overflow:hidden;}

    .sidebar{width:220px;background:var(--surface);display:flex;flex-direction:column;padding:1rem 0;flex-shrink:0;}
    .sidebar-logo{padding:.5rem 1.5rem 1.5rem;font-size:1.1rem;font-weight:700;color:var(--accent);}
    .sidebar-logo span{color:var(--accent2);}
    .nav-item{padding:.75rem 1.5rem;cursor:pointer;transition:background .2s;display:flex;align-items:center;gap:.75rem;font-size:.9rem;color:var(--muted);border-left:3px solid transparent;}
    .nav-item:hover{background:var(--surface2);color:var(--text);}
    .nav-item.active{background:var(--surface2);color:var(--accent);border-left-color:var(--accent);}
    .sidebar-footer{margin-top:auto;padding:1rem 1.5rem;font-size:.75rem;color:var(--muted);}

    .main{flex:1;overflow-y:auto;padding:1.5rem;}
    .section{display:none;}
    .section.active{display:block;}
    h2{font-size:1.3rem;margin-bottom:1.25rem;}
    h3{font-size:1rem;margin-bottom:.75rem;color:var(--muted);}

    .card-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin-bottom:1.5rem;}
    .card{background:var(--surface);border-radius:10px;padding:1.1rem 1.25rem;}
    .card-label{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}
    .card-value{font-size:1.5rem;font-weight:700;margin-top:.4rem;}

    .table-wrap{background:var(--surface);border-radius:10px;overflow:hidden;margin-bottom:1.25rem;}
    table{width:100%;border-collapse:collapse;font-size:.85rem;}
    th{background:var(--surface2);padding:.75rem 1rem;text-align:left;font-weight:600;color:var(--muted);text-transform:uppercase;font-size:.7rem;letter-spacing:.05em;}
    td{padding:.75rem 1rem;border-bottom:1px solid var(--surface2);}
    tr:last-child td{border-bottom:none;}
    tr:hover td{background:rgba(255,255,255,.02);}

    .badge{display:inline-block;padding:.2em .6em;border-radius:4px;font-size:.75rem;font-weight:600;}
    .badge-green{background:rgba(34,197,94,.15);color:var(--green);}
    .badge-red{background:rgba(239,68,68,.15);color:var(--red);}
    .badge-amber{background:rgba(245,158,11,.15);color:var(--amber);}
    .badge-blue{background:rgba(108,99,255,.15);color:var(--accent);}
    .badge-teal{background:rgba(0,212,170,.15);color:var(--accent2);}
    .badge-gray{background:rgba(100,116,139,.15);color:var(--muted);}

    .btn{padding:.5rem 1.1rem;border:none;border-radius:6px;cursor:pointer;font-size:.85rem;font-weight:600;transition:opacity .2s;}
    .btn-primary{background:var(--accent);color:#fff;}
    .btn-teal{background:var(--accent2);color:#000;}
    .btn-danger{background:var(--danger);color:#fff;}
    .btn-sm{padding:.3rem .75rem;font-size:.78rem;}
    .btn:hover{opacity:.85;}
    .btn:disabled{opacity:.4;cursor:not-allowed;}

    .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;}
    .dot-green{background:var(--green);box-shadow:0 0 6px var(--green);}
    .dot-red{background:var(--red);}
    .dot-amber{background:var(--amber);animation:pulse 1.5s infinite;}
    @keyframes pulse{0%,100%{opacity:1;}50%{opacity:.4;}}

    .ws-bar{background:var(--surface);border-radius:8px;padding:.5rem 1rem;margin-bottom:1.25rem;font-size:.8rem;color:var(--muted);display:flex;align-items:center;gap:.5rem;}
    .chart-box{background:var(--surface);border-radius:10px;padding:1.25rem;margin-bottom:1.5rem;}
    .chart-box canvas{max-height:300px;}

    /* Backtesting */
    .bt-controls{background:var(--surface);border-radius:10px;padding:1.25rem;margin-bottom:1.25rem;display:flex;gap:1rem;align-items:flex-end;flex-wrap:wrap;}
    .bt-controls label{display:flex;flex-direction:column;gap:.35rem;font-size:.8rem;color:var(--muted);}
    .bt-controls select,.bt-controls input{background:var(--surface2);color:var(--text);border:1px solid var(--surface2);border-radius:6px;padding:.45rem .75rem;font-size:.85rem;}
    .bt-progress{background:var(--surface);border-radius:10px;padding:1.25rem;text-align:center;color:var(--muted);display:none;}
    .bt-results{display:none;}
    .mc-box{background:var(--surface);border-radius:10px;padding:1.25rem;margin-top:1.25rem;display:none;}

    /* Modal */
    .modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:100;}
    .modal-overlay.open{display:flex;}
    .modal{background:var(--surface);border-radius:12px;padding:1.75rem;width:480px;max-width:95vw;max-height:90vh;overflow-y:auto;}
    .modal h3{margin-bottom:1.25rem;font-size:1.1rem;color:var(--text);}
    .form-grid{display:grid;grid-template-columns:1fr 1fr;gap:.75rem;}
    .form-field{display:flex;flex-direction:column;gap:.3rem;font-size:.82rem;color:var(--muted);}
    .form-field.full{grid-column:1/-1;}
    .form-field input,.form-field select{background:var(--surface2);color:var(--text);border:1px solid rgba(255,255,255,.08);border-radius:6px;padding:.5rem .75rem;font-size:.85rem;}
    .modal-actions{display:flex;gap:.75rem;margin-top:1.25rem;justify-content:flex-end;}

    /* Account connection dot */
    .conn-dot{width:8px;height:8px;border-radius:50%;display:inline-block;}
    .conn-dot.on{background:var(--green);box-shadow:0 0 5px var(--green);}
    .conn-dot.off{background:var(--red);}
  </style>
</head>
<body>

<!-- ══ SIDEBAR ══ -->
<nav class="sidebar">
  <div class="sidebar-logo">Claude<span>TradingBot</span></div>
  <div class="nav-item active"  onclick="showSection('dashboard',this)">📊 Dashboard</div>
  <div class="nav-item" onclick="showSection('signals',this)">📡 Signals</div>
  <div class="nav-item" onclick="showSection('trades',this)">💼 Trades</div>
  <div class="nav-item" onclick="showSection('performance',this)">📈 Performance</div>
  <div class="nav-item" onclick="showSection('settings',this)">⚙️ Settings</div>
  <div class="nav-item" onclick="showSection('backtesting',this)">🔬 Backtesting</div>
  <div class="nav-item" onclick="showSection('accounts',this)">🏦 Accounts</div>
  <div class="sidebar-footer" id="ws-footer">WS: —</div>
</nav>

<!-- ══ MAIN ══ -->
<main class="main">
  <div class="ws-bar"><span class="dot dot-amber" id="ws-dot"></span><span id="ws-label">Connecting…</span></div>

  <!-- 1. Dashboard -->
  <section class="section active" id="section-dashboard">
    <h2>Overview</h2>
    <div class="card-grid">
      <div class="card"><div class="card-label">Bot Mode</div><div class="card-value" id="bot-mode">—</div></div>
      <div class="card"><div class="card-label">Open Positions</div><div class="card-value" id="open-pos">—</div></div>
      <div class="card"><div class="card-label">Signals Today</div><div class="card-value" id="sigs-today">—</div></div>
      <div class="card"><div class="card-label">Trades Today</div><div class="card-value" id="trades-today">—</div></div>
      <div class="card"><div class="card-label">Daily P&L</div><div class="card-value" id="daily-pnl">—</div></div>
      <div class="card"><div class="card-label">Win Rate</div><div class="card-value" id="win-rate">—</div></div>
    </div>
    <div class="chart-box"><canvas id="pnl-chart"></canvas></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Time</th><th>Pair</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP1</th><th>RR</th><th>Status</th></tr></thead>
      <tbody id="recent-signals-body"><tr><td colspan="8" style="text-align:center;color:var(--muted)">Loading…</td></tr></tbody>
    </table></div>
  </section>

  <!-- 2. Signals -->
  <section class="section" id="section-signals">
    <h2>Trade Signals</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Time</th><th>Pair</th><th>TF</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP1</th><th>TP2</th><th>Conf%</th><th>Strategy</th><th>Status</th></tr></thead>
      <tbody id="signals-body"><tr><td colspan="11" style="text-align:center;color:var(--muted)">No signals yet</td></tr></tbody>
    </table></div>
  </section>

  <!-- 3. Trades -->
  <section class="section" id="section-trades">
    <h2>Executed Trades</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Time</th><th>Pair</th><th>Dir</th><th>Lot</th><th>Entry</th><th>SL</th><th>TP</th><th>Profit</th><th>Status</th><th>Ticket</th></tr></thead>
      <tbody id="trades-body"><tr><td colspan="10" style="text-align:center;color:var(--muted)">No trades yet</td></tr></tbody>
    </table></div>
  </section>

  <!-- 4. Performance -->
  <section class="section" id="section-performance">
    <h2>Performance</h2>
    <div class="card-grid">
      <div class="card"><div class="card-label">Total Trades</div><div class="card-value" id="p-total">—</div></div>
      <div class="card"><div class="card-label">Win Rate</div><div class="card-value" id="p-wr">—</div></div>
      <div class="card"><div class="card-label">Net P&L</div><div class="card-value" id="p-pnl">—</div></div>
      <div class="card"><div class="card-label">Profit Factor</div><div class="card-value" id="p-pf">—</div></div>
      <div class="card"><div class="card-label">Max Drawdown</div><div class="card-value" id="p-dd">—</div></div>
      <div class="card"><div class="card-label">Avg R:R</div><div class="card-value" id="p-rr">—</div></div>
    </div>
    <div class="chart-box"><canvas id="perf-chart"></canvas></div>
  </section>

  <!-- 5. Settings -->
  <section class="section" id="section-settings">
    <h2>Settings</h2>
    <div style="background:var(--surface);border-radius:10px;padding:1.5rem;max-width:500px;">
      <p style="color:var(--muted);font-size:.9rem;margin-bottom:1rem;">Runtime configuration. Changes apply to next scan cycle.</p>
      <table><tbody>
        <tr><td>Bot Mode</td><td id="cfg-mode">—</td></tr>
        <tr><td>Risk Per Trade</td><td id="cfg-risk">—</td></tr>
        <tr><td>Min RR Ratio</td><td id="cfg-rr">—</td></tr>
        <tr><td>Max Positions</td><td id="cfg-maxpos">—</td></tr>
      </tbody></table>
      <div style="margin-top:1.5rem;display:flex;gap:.75rem;">
        <button class="btn btn-teal" onclick="resumeBot()">▶ Resume</button>
        <button class="btn btn-danger" onclick="pauseBot()">⏸ Pause</button>
      </div>
    </div>
  </section>

  <!-- 6. Backtesting -->
  <section class="section" id="section-backtesting">
    <h2>Backtesting</h2>
    <div class="bt-controls">
      <label>Symbol
        <select id="bt-symbol"><option>XAUUSD</option><option>EURUSD</option><option>GBPUSD</option><option>USDJPY</option><option>BTCUSD</option><option>NAS100</option><option>US30</option></select>
      </label>
      <label>Strategy<select id="bt-strategy"><option value="swing">Swing</option><option value="scalping">Scalping</option></select></label>
      <label>Timeframe<select id="bt-timeframe"><option>H4</option><option>H1</option><option>D1</option><option>M15</option><option>M5</option></select></label>
      <label>Bars<input type="number" id="bt-count" value="5000" min="500" max="50000" style="width:100px"/></label>
      <label>Init Cash ($)<input type="number" id="bt-cash" value="10000" min="1000" style="width:120px"/></label>
      <button class="btn btn-primary" id="bt-run-btn" onclick="runBacktest()">▶ Run</button>
      <button class="btn btn-teal" onclick="compareAllPairs()">🌐 All Pairs</button>
    </div>
    <div class="bt-progress" id="bt-progress"><span class="dot dot-amber"></span> Running backtest… please wait</div>
    <div class="bt-results" id="bt-results">
      <div class="card-grid" id="bt-stat-cards"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.25rem;">
        <div class="chart-box"><canvas id="bt-equity-chart"></canvas></div>
        <div class="chart-box"><canvas id="bt-dist-chart"></canvas></div>
      </div>
      <div style="display:flex;gap:.75rem;margin-bottom:1rem;">
        <button class="btn btn-primary" onclick="runMonteCarlo()" id="mc-btn">🎲 Monte Carlo</button>
        <button class="btn btn-teal" onclick="optimizeParams()" id="opt-btn">🔧 Optimize</button>
      </div>
    </div>
    <div class="mc-box" id="mc-box">
      <h3>Monte Carlo Results</h3>
      <div class="card-grid" id="mc-stat-cards"></div>
      <div class="chart-box" style="margin-top:1rem;"><canvas id="mc-fan-chart"></canvas></div>
    </div>
    <div id="all-pairs-box" style="display:none;margin-top:1.25rem;">
      <h3>All Pairs Comparison</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Pair</th><th>Strategy</th><th>Trades</th><th>Win%</th><th>Net P&L</th><th>Profit Factor</th><th>Max DD%</th><th>Sharpe</th></tr></thead>
        <tbody id="all-pairs-body"></tbody>
      </table></div>
    </div>
  </section>

  <!-- 7. Accounts -->
  <section class="section" id="section-accounts">
    <h2>Multi-Account Management</h2>

    <!-- Aggregated overview -->
    <div class="card-grid" id="agg-cards">
      <div class="card"><div class="card-label">Total Equity</div><div class="card-value" id="agg-equity">—</div></div>
      <div class="card"><div class="card-label">Total Positions</div><div class="card-value" id="agg-pos">—</div></div>
      <div class="card"><div class="card-label">Total Daily P&L</div><div class="card-value" id="agg-pnl">—</div></div>
      <div class="card"><div class="card-label">Connected</div><div class="card-value" id="agg-connected">—</div></div>
    </div>

    <!-- Actions -->
    <div style="display:flex;gap:.75rem;margin-bottom:1.25rem;">
      <button class="btn btn-primary" onclick="openAddAccountModal()">+ Add Account</button>
      <button class="btn btn-teal"    onclick="loadAccounts()">↺ Refresh</button>
    </div>

    <!-- Accounts table -->
    <div class="table-wrap"><table>
      <thead><tr><th></th><th>Label</th><th>Login</th><th>Broker</th><th>Type</th><th>Equity</th><th>Positions</th><th>Daily P&L</th><th>Lot×</th><th>Actions</th></tr></thead>
      <tbody id="accounts-body"><tr><td colspan="10" style="text-align:center;color:var(--muted)">Loading…</td></tr></tbody>
    </table></div>

    <!-- Copy Performance (shown if followers exist) -->
    <div id="copy-perf-box" style="display:none;">
      <h3 style="margin-bottom:.75rem;">Copy Trading Performance</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Account</th><th>Trades</th><th>Win Rate</th><th>Net P&L</th></tr></thead>
        <tbody id="copy-perf-body"></tbody>
      </table></div>
    </div>
  </section>

</main>

<!-- ══ ADD ACCOUNT MODAL ══ -->
<div class="modal-overlay" id="add-account-modal">
  <div class="modal">
    <h3>Add MT5 Account</h3>
    <div class="form-grid">
      <div class="form-field full"><label>Account ID (slug)<input id="f-id" placeholder="exness_main"/></label></div>
      <div class="form-field full"><label>Label<input id="f-label" placeholder="Exness Master"/></label></div>
      <div class="form-field"><label>MT5 Login<input id="f-login" type="number" placeholder="12345678"/></label></div>
      <div class="form-field"><label>Password<input id="f-password" type="password"/></label></div>
      <div class="form-field full"><label>Server<input id="f-server" placeholder="Exness-MT5Real"/></label></div>
      <div class="form-field"><label>Broker<input id="f-broker" value="Exness"/></label></div>
      <div class="form-field"><label>Type
        <select id="f-type"><option value="false">Follower</option><option value="true">Master</option></select>
      </label></div>
      <div class="form-field"><label>Risk % per trade<input id="f-risk" type="number" value="1.0" step="0.1" min="0.1" max="10"/></label></div>
      <div class="form-field"><label>Lot Multiplier<input id="f-lot" type="number" value="1.0" step="0.1" min="0.1"/></label></div>
      <div class="form-field"><label>Copy Delay (sec)<input id="f-delay" type="number" value="0" min="0"/></label></div>
      <div class="form-field"><label>Max Positions<input id="f-maxpos" type="number" value="5" min="1"/></label></div>
      <div class="form-field"><label>Magic Offset<input id="f-magic" type="number" value="0" min="0"/></label></div>
      <div class="form-field full">
        <label style="color:var(--warn);">Bot API Token (required to save)
          <input id="f-token" type="password" placeholder="Enter BOT_API_TOKEN"/>
        </label>
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeAddAccountModal()" style="background:var(--surface2)">Cancel</button>
      <button class="btn btn-primary" onclick="submitAddAccount()">Add Account</button>
    </div>
  </div>
</div>

<script>
// ══════════════════════════════════════════════════════════
const API = '';
let _pnlChart=null,_perfChart=null,_btEquityChart=null,_btDistChart=null,_mcFanChart=null;
let _lastBtStats=null,_btJobId=null,_btPollTimer=null;

function $(id){return document.getElementById(id);}
function showSection(name,el){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  $('section-'+name).classList.add('active');
  el.classList.add('active');
  if(name==='performance') loadPerformance();
  if(name==='signals')     loadSignals();
  if(name==='trades')      loadTrades();
  if(name==='accounts')    loadAccounts();
}
async function api(path,opts={}){
  try{const r=await fetch(API+path,opts);return await r.json();}
  catch(e){console.warn('API',path,e);return null;}
}
function fmtNum(v,dec=2){return v==null?'—':Number(v).toFixed(dec);}
function colorVal(v){return v>0?'var(--green)':v<0?'var(--red)':'var(--text)';}
function sharpeBadge(v){
  if(v==null) return '<span class="badge badge-amber">N/A</span>';
  const n=Number(v);
  if(n>=1)   return `<span class="badge badge-green">${n.toFixed(2)}</span>`;
  if(n>=0.5) return `<span class="badge badge-amber">${n.toFixed(2)}</span>`;
  return `<span class="badge badge-red">${n.toFixed(2)}</span>`;
}

// ── WebSocket ─────────────────────────────────────────────────────────────
let wsConn=null;
function connectWS(){
  const proto=location.protocol==='https:'?'wss':'ws';
  wsConn=new WebSocket(`${proto}://${location.host}/ws`);
  wsConn.onopen =()=>setWS('Connected','dot-green');
  wsConn.onclose=()=>{setWS('Disconnected','dot-red');setTimeout(connectWS,5000);};
  wsConn.onerror=()=>setWS('Error','dot-red');
  wsConn.onmessage=e=>{
    try{
      const msg=JSON.parse(e.data);
      if(msg.event==='new_signal')   prependSignalRow(msg.data);
      if(msg.event==='trade_event')  prependTradeRow(msg.data);
      if(msg.event==='status_update') updateStatusCards(msg.data);
    }catch{}
  };
}
function setWS(label,cls){$('ws-label').textContent='WS: '+label;$('ws-footer').textContent='WS: '+label;$('ws-dot').className='dot '+cls;}

// ── Dashboard ─────────────────────────────────────────────────────────────
async function loadDashboard(){
  const [status,perf]=await Promise.all([api('/status'),api('/performance?days=1')]);
  if(status) updateStatusCards(status);
  if(perf){$('daily-pnl').textContent='$'+(perf.net_pnl||0).toFixed(2);$('daily-pnl').style.color=colorVal(perf.net_pnl);$('win-rate').textContent=((perf.win_rate||0)*100).toFixed(1)+'%';}
  const sigs=await api('/signals?limit=10');
  if(sigs&&sigs.signals) renderRecentSignals(sigs.signals);
}
function updateStatusCards(s){
  if(!s)return;
  $('bot-mode').textContent=s.mode||'—';$('open-pos').textContent=s.open_positions??'—';
  $('sigs-today').textContent=s.signals_today??'—';$('trades-today').textContent=s.trades_today??'—';
}
function renderRecentSignals(sigs){
  const tb=$('recent-signals-body');
  if(!sigs.length){tb.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--muted)">No signals</td></tr>';return;}
  tb.innerHTML=sigs.map(s=>`<tr>
    <td>${new Date(s.created_at||Date.now()).toLocaleTimeString()}</td>
    <td><b>${s.symbol||'—'}</b></td>
    <td><span class="badge ${s.direction==='BUY'?'badge-green':'badge-red'}">${s.direction||'—'}</span></td>
    <td>${fmtNum(s.entry)}</td><td>${fmtNum(s.stop_loss)}</td><td>${fmtNum(s.take_profit_1)}</td>
    <td>${fmtNum(s.rr_ratio,1)}</td><td><span class="badge badge-blue">${s.status||'PENDING'}</span></td>
  </tr>`).join('');
}
function prependSignalRow(s){renderRecentSignals([s]);}
function prependTradeRow(t){}

// ── Signals / Trades ──────────────────────────────────────────────────────
async function loadSignals(){
  const d=await api('/signals?limit=100');if(!d)return;
  const tb=$('signals-body');
  if(!d.signals.length){tb.innerHTML='<tr><td colspan="11" style="text-align:center;color:var(--muted)">No signals</td></tr>';return;}
  tb.innerHTML=d.signals.map(s=>`<tr>
    <td>${new Date(s.created_at||Date.now()).toLocaleString()}</td>
    <td><b>${s.symbol}</b></td><td>${s.timeframe||'—'}</td>
    <td><span class="badge ${s.direction==='BUY'?'badge-green':'badge-red'}">${s.direction}</span></td>
    <td>${fmtNum(s.entry)}</td><td>${fmtNum(s.stop_loss)}</td><td>${fmtNum(s.take_profit_1)}</td><td>${fmtNum(s.take_profit_2)}</td>
    <td>${fmtNum(s.confidence,0)}%</td><td>${s.strategy||'—'}</td><td><span class="badge badge-blue">${s.status||'PENDING'}</span></td>
  </tr>`).join('');
}
async function loadTrades(){
  const d=await api('/trades?limit=100');if(!d)return;
  const tb=$('trades-body');
  if(!d.trades.length){tb.innerHTML='<tr><td colspan="10" style="text-align:center;color:var(--muted)">No trades</td></tr>';return;}
  tb.innerHTML=d.trades.map(t=>`<tr>
    <td>${new Date(t.created_at||Date.now()).toLocaleString()}</td>
    <td><b>${t.symbol}</b></td>
    <td><span class="badge ${t.direction==='BUY'?'badge-green':'badge-red'}">${t.direction}</span></td>
    <td>${fmtNum(t.lot_size,2)}</td><td>${fmtNum(t.entry_price)}</td><td>${fmtNum(t.stop_loss)}</td><td>${fmtNum(t.take_profit_1)}</td>
    <td style="color:${colorVal(t.profit)}">${fmtNum(t.profit,2)}</td>
    <td><span class="badge badge-teal">${t.status||'OPEN'}</span></td>
    <td style="font-size:.75rem;color:var(--muted)">${t.mt5_ticket||'—'}</td>
  </tr>`).join('');
}

// ── Performance ───────────────────────────────────────────────────────────
async function loadPerformance(){
  const d=await api('/performance?days=30');if(!d)return;
  $('p-total').textContent=d.total_trades??'—';$('p-wr').textContent=d.win_rate?(d.win_rate*100).toFixed(1)+'%':'—';
  $('p-pnl').textContent=d.net_pnl?'$'+d.net_pnl.toFixed(2):'—';$('p-pnl').style.color=colorVal(d.net_pnl);
  $('p-pf').textContent=fmtNum(d.profit_factor);$('p-dd').textContent=d.max_drawdown?d.max_drawdown.toFixed(1)+'%':'—';$('p-rr').textContent=fmtNum(d.avg_rr,1);
}

// ── Bot controls ──────────────────────────────────────────────────────────
async function pauseBot(){await api('/pause',{method:'POST'});$('bot-mode').textContent='PAUSED';}
async function resumeBot(){await api('/resume',{method:'POST'});loadDashboard();}

// ══════════════════════════════════════════════════════════
// PHASE 3 — Backtesting
// ══════════════════════════════════════════════════════════
async function runBacktest(){
  const symbol=$('bt-symbol').value,strategy=$('bt-strategy').value,tf=$('bt-timeframe').value,count=$('bt-count').value,cash=$('bt-cash').value;
  $('bt-run-btn').disabled=true;$('bt-progress').style.display='block';$('bt-results').style.display='none';$('mc-box').style.display='none';
  const d=await api(`/backtest/run?symbol=${symbol}&strategy=${strategy}&timeframe=${tf}&count=${count}&init_cash=${cash}`);
  if(!d||!d.job_id){alert('Backtest failed');$('bt-run-btn').disabled=false;return;}
  _btJobId=d.job_id;_btPollTimer=setInterval(pollBtResult,2000);
}
async function pollBtResult(){
  if(!_btJobId)return;const d=await api(`/backtest/result/${_btJobId}`);if(!d)return;
  if(d.status==='done'){clearInterval(_btPollTimer);$('bt-progress').style.display='none';$('bt-run-btn').disabled=false;renderBtResults(d.result);}
  else if(d.status==='error'){clearInterval(_btPollTimer);$('bt-progress').style.display='none';$('bt-run-btn').disabled=false;alert('Error: '+(d.error||'Unknown'));}
}
function renderBtResults(r){
  if(!r)return;_lastBtStats=r;$('bt-results').style.display='block';
  const cards=[['Total Trades',r.total_trades,''],['Win Rate',((r.win_rate||0)*100).toFixed(1)+'%',r.win_rate>=0.5?'green':'red'],
    ['Net P&L','$'+fmtNum(r.net_pnl),r.net_pnl>=0?'green':'red'],['Profit Factor',fmtNum(r.profit_factor),r.profit_factor>=1.2?'green':'amber'],
    ['Max Drawdown',fmtNum(r.max_drawdown_pct)+'%',r.max_drawdown_pct<=15?'green':'red'],['Sharpe',fmtNum(r.sharpe_ratio),r.sharpe_ratio>=1?'green':'amber']];
  $('bt-stat-cards').innerHTML=cards.map(([l,v,c])=>`<div class="card"><div class="card-label">${l}</div><div class="card-value" style="color:${c?'var(--'+c+')':'var(--text)'}">${v}</div></div>`).join('');
  const returns=r.trade_returns||[];let eq=parseFloat($('bt-cash').value)||10000;const curve=[eq];
  returns.forEach(ret=>{eq*=1+ret/100;curve.push(parseFloat(eq.toFixed(2)));});
  renderBtEquityChart(curve);renderBtDistChart(returns);
}
function renderBtEquityChart(curve){
  const ctx=$('bt-equity-chart').getContext('2d');if(_btEquityChart)_btEquityChart.destroy();
  _btEquityChart=new Chart(ctx,{type:'line',data:{labels:curve.map((_,i)=>i),datasets:[{label:'Equity',data:curve,borderColor:'#6c63ff',fill:true,backgroundColor:'rgba(108,99,255,.1)',tension:.3,pointRadius:0}]},
    options:{responsive:true,plugins:{legend:{display:false},title:{display:true,text:'Equity Curve',color:'#e2e8f0'}},scales:{x:{display:false},y:{grid:{color:'rgba(255,255,255,.05)'}}}}});
}
function renderBtDistChart(returns){
  const ctx=$('bt-dist-chart').getContext('2d');if(_btDistChart)_btDistChart.destroy();
  const bins={};returns.forEach(r=>{const b=Math.round(r/0.5)*0.5;bins[b]=(bins[b]||0)+1;});
  const labels=Object.keys(bins).map(Number).sort((a,b)=>a-b);
  _btDistChart=new Chart(ctx,{type:'bar',data:{labels:labels.map(l=>l+'%'),datasets:[{data:labels.map(l=>bins[l]),backgroundColor:labels.map(l=>l>=0?'rgba(34,197,94,.7)':'rgba(239,68,68,.7)')}]},
    options:{responsive:true,plugins:{legend:{display:false},title:{display:true,text:'Trade Distribution',color:'#e2e8f0'}},scales:{x:{grid:{color:'rgba(255,255,255,.04)'}},y:{grid:{color:'rgba(255,255,255,.04)'}}}}});
}
async function runMonteCarlo(){
  if(!_lastBtStats){alert('Run backtest first');return;}
  $('mc-btn').disabled=true;$('mc-btn').textContent='⏳ Simulating…';
  const d=await api('/backtest/monte-carlo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:$('bt-symbol').value,strategy:$('bt-strategy').value,n_simulations:1000,init_cash:parseFloat($('bt-cash').value)||10000})});
  $('mc-btn').disabled=false;$('mc-btn').textContent='🎲 Monte Carlo';
  if(!d||!d.monte_carlo){alert('Monte Carlo failed');return;}renderMcResults(d.monte_carlo);
}
function renderMcResults(mc){
  $('mc-box').style.display='block';
  $('mc-stat-cards').innerHTML=[['Expected Return',mc.expected_return_pct+'%',mc.expected_return_pct>=0?'green':'red'],
    ['Median Equity','$'+mc.median_final_equity,''],['P5 Equity','$'+mc.p5_final_equity,'red'],['P95 Equity','$'+mc.p95_final_equity,'green'],
    ['P(Loss)',(mc.probability_of_loss*100).toFixed(1)+'%',mc.probability_of_loss>0.4?'red':'amber'],
    ['P(Ruin 3%)',(mc.probability_of_ruin*100).toFixed(1)+'%',mc.probability_of_ruin>0.2?'red':'green']]
    .map(([l,v,c])=>`<div class="card"><div class="card-label">${l}</div><div class="card-value" style="color:${c?'var(--'+c+')':'var(--text)'}">${v}</div></div>`).join('');
  const curves=mc.sample_equity_curves||[];const maxLen=curves.reduce((m,c)=>Math.max(m,c.length),0);
  const ctx=$('mc-fan-chart').getContext('2d');if(_mcFanChart)_mcFanChart.destroy();
  _mcFanChart=new Chart(ctx,{type:'line',data:{labels:Array.from({length:maxLen},(_,i)=>i),datasets:curves.slice(0,50).map(c=>({data:c,borderColor:'rgba(108,99,255,.15)',borderWidth:1,fill:false,pointRadius:0,tension:.3}))},
    options:{responsive:true,animation:false,plugins:{legend:{display:false},title:{display:true,text:'Monte Carlo Fan (50 Scenarios)',color:'#e2e8f0'}},scales:{x:{display:false},y:{grid:{color:'rgba(255,255,255,.05)'}}}}});
}
async function optimizeParams(){
  const symbol=$('bt-symbol').value,strategy=$('bt-strategy').value,tf=$('bt-timeframe').value;
  $('opt-btn').disabled=true;$('opt-btn').textContent='⏳ Optimizing…';
  const d=await api('/backtest/optimize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol,strategy,timeframe:tf})});
  $('opt-btn').disabled=false;$('opt-btn').textContent='🔧 Optimize';
  if(!d||!d.job_id){alert('Optimize failed');return;}
  const poll=async()=>{const r=await api(`/backtest/result/${d.job_id}`);if(!r)return;if(r.status==='done'){alert(`Best params:\\n${JSON.stringify(r.result?.best_params,null,2)}`);}else if(r.status==='error'){alert('Error: '+(r.error||'unknown'));}else setTimeout(poll,2000);};
  setTimeout(poll,2000);
}
async function compareAllPairs(){
  const strategy=$('bt-strategy').value;$('all-pairs-box').style.display='none';
  const d=await api(`/backtest/all-pairs?strategy=${strategy}`);if(!d||!Array.isArray(d))return;
  $('all-pairs-body').innerHTML=d.map(r=>`<tr><td><b>${r.symbol||'—'}</b></td><td>${r.strategy||strategy}</td><td>${r.total_trades??0}</td>
    <td>${r.win_rate?(r.win_rate*100).toFixed(1)+'%':'—'}</td><td style="color:${colorVal(r.net_pnl)}">${r.net_pnl!=null?'$'+fmtNum(r.net_pnl):'—'}</td>
    <td>${fmtNum(r.profit_factor)}</td><td>${fmtNum(r.max_drawdown_pct)}%</td><td>${sharpeBadge(r.sharpe_ratio)}</td></tr>`).join('');
  $('all-pairs-box').style.display='block';
}

// ══════════════════════════════════════════════════════════
// PHASE 4 — Accounts
// ══════════════════════════════════════════════════════════
async function loadAccounts(){
  const [accounts, agg] = await Promise.all([api('/accounts'), api('/accounts/aggregated')]);

  // Aggregated cards
  if(agg){
    $('agg-equity').textContent   = '$'+(agg.total_equity||0).toFixed(2);
    $('agg-pos').textContent      = agg.total_positions??'—';
    $('agg-pnl').textContent      = '$'+(agg.total_daily_pnl||0).toFixed(2);
    $('agg-pnl').style.color      = colorVal(agg.total_daily_pnl);
    $('agg-connected').textContent= `${agg.accounts_connected||0} / ${agg.accounts_total||0}`;
  }

  if(!accounts||!accounts.accounts){$('accounts-body').innerHTML='<tr><td colspan="10" style="text-align:center;color:var(--muted)">No accounts</td></tr>';return;}

  // Merge live info from aggregated
  const liveMap = {};
  if(agg&&agg.accounts) agg.accounts.forEach(a=>{liveMap[a.account_id]=a;});

  const tb=$('accounts-body');
  if(!accounts.accounts.length){tb.innerHTML='<tr><td colspan="10" style="text-align:center;color:var(--muted)">No accounts registered</td></tr>';return;}

  tb.innerHTML=accounts.accounts.map(a=>{
    const live=liveMap[a.account_id]||{};
    const connected=live.is_connected;
    const connDot=`<span class="conn-dot ${connected?'on':'off'}"></span>`;
    const typeBadge=a.is_master?'<span class="badge badge-amber">Master</span>':'<span class="badge badge-teal">Follower</span>';
    const equity=live.equity!=null?'$'+live.equity.toFixed(2):'—';
    const positions=live.positions??'—';
    const pnl=live.daily_pnl!=null?`<span style="color:${colorVal(live.daily_pnl)}">$${live.daily_pnl.toFixed(2)}</span>`:'—';
    const connectBtn=connected
      ?`<button class="btn btn-danger btn-sm" onclick="disconnectAccount('${a.account_id}')">Disconnect</button>`
      :`<button class="btn btn-teal btn-sm" onclick="connectAccount('${a.account_id}')">Connect</button>`;
    const deleteBtn=`<button class="btn btn-sm" style="background:var(--surface2);margin-left:.35rem;" onclick="deactivateAccount('${a.account_id}')">✕</button>`;
    return `<tr>
      <td>${connDot}</td>
      <td><b>${a.label}</b></td>
      <td style="font-size:.8rem;color:var(--muted)">${a.login}</td>
      <td>${a.broker||'—'}</td>
      <td>${typeBadge}</td>
      <td>${equity}</td>
      <td>${positions}</td>
      <td>${pnl}</td>
      <td>${a.lot_size_multiplier||1}×</td>
      <td>${connectBtn}${deleteBtn}</td>
    </tr>`;
  }).join('');

  // Copy performance (if followers exist)
  const followers = accounts.accounts.filter(a=>!a.is_master);
  if(followers.length>0){
    const cp = await api('/accounts/copy-performance');
    if(cp&&cp.followers&&cp.followers.length>0){
      $('copy-perf-body').innerHTML=cp.followers.map(f=>`<tr>
        <td>${f.account_id}</td><td>${f.trades}</td>
        <td>${(f.win_rate*100).toFixed(1)}%</td>
        <td style="color:${colorVal(f.net_profit)}">$${f.net_profit.toFixed(2)}</td>
      </tr>`).join('');
      $('copy-perf-box').style.display='block';
    }
  }
}

async function connectAccount(id){const r=await api(`/accounts/${id}/connect`,{method:'POST'});if(r)loadAccounts();}
async function disconnectAccount(id){const r=await api(`/accounts/${id}/disconnect`,{method:'POST'});if(r)loadAccounts();}
async function deactivateAccount(id){
  if(!confirm(`Deactivate account ${id}?`))return;
  const token=getStoredToken();
  const r=await api(`/accounts/${id}`,{method:'DELETE',headers:token?{'X-Bot-Token':token}:{}});
  if(r)loadAccounts();
}

function openAddAccountModal(){$('add-account-modal').classList.add('open');}
function closeAddAccountModal(){$('add-account-modal').classList.remove('open');}

function getStoredToken(){return sessionStorage.getItem('bot_api_token')||'';}
function storeToken(t){if(t)sessionStorage.setItem('bot_api_token',t);}

async function submitAddAccount(){
  const token=$('f-token').value.trim();
  storeToken(token);
  const payload={
    account_id:$('f-id').value.trim(),label:$('f-label').value.trim(),
    login:parseInt($('f-login').value),password:$('f-password').value,
    server:$('f-server').value.trim(),broker:$('f-broker').value.trim(),
    is_master:$('f-type').value==='true',risk_per_trade_pct:parseFloat($('f-risk').value)||1,
    lot_size_multiplier:parseFloat($('f-lot').value)||1,copy_delay_seconds:parseInt($('f-delay').value)||0,
    max_positions:parseInt($('f-maxpos').value)||5,magic_number_offset:parseInt($('f-magic').value)||0,
  };
  if(!payload.account_id||!payload.login||!payload.password||!payload.server){alert('Please fill all required fields');return;}
  const r=await api('/accounts',{method:'POST',headers:{'Content-Type':'application/json','X-Bot-Token':token},body:JSON.stringify(payload)});
  if(r&&r.status==='added'){closeAddAccountModal();loadAccounts();}
  else{alert('Failed to add account: '+(r?.detail||JSON.stringify(r)));}
}

// ══════════════════════════════════════════════════════════
// Init
// ══════════════════════════════════════════════════════════
connectWS();
loadDashboard();
setInterval(loadDashboard,30000);
</script>
</body>
</html>
""")

print()
print("=" * 60)
print("Phase 4 Bootstrap COMPLETE")
print("=" * 60)
print()
print("Files created:")
print("  multi_account/__init__.py")
print("  multi_account/account_registry.py  (MT5Account + Fernet encryption)")
print("  multi_account/account_manager.py   (AccountManager pool)")
print("  multi_account/copy_engine.py       (CopyEngine + CopyTradeRecord)")
print("  api/routes.py                      (updated with /accounts/* endpoints)")
print("  dashboard/index.html               (updated with Accounts tab)")
print()
print("New env vars needed (.env):")
print("  ACCOUNT_ENCRYPTION_KEY  — run: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
print("  BOT_API_TOKEN           — any secret string for protecting POST /accounts")
print()
print("New pip packages needed:")
print("  pip install cryptography")
