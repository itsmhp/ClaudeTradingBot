"""
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
