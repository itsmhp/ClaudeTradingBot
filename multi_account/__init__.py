"""
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
