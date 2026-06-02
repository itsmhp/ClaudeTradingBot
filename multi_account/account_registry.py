"""
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
