import dataclasses
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AccountConfig, AppConfig
from .mailbox import OTPMailbox

logger = logging.getLogger(__name__)


@dataclass
class Account:
    """Runtime state for one VFS login: its config, where its cookies live,
    and when (if ever) it's allowed to be used again after a block."""

    config: AccountConfig
    storage_state_path: str
    cooldown_until: float = 0.0
    consecutive_blocks: int = 0

    @property
    def label(self) -> str:
        return self.config.label or self.config.vfs_email or "account"

    def is_available(self, now: float) -> bool:
        return now >= self.cooldown_until


class AccountPool:
    """Round-robins over the available accounts. A blocked account is parked
    until its cooldown expires; rotation always prefers a *different* account
    and only falls back to the current one when it's the only one free."""

    def __init__(self, accounts: list[Account], clock=time.time):
        if not accounts:
            raise ValueError("AccountPool requires at least one account")
        self.accounts = accounts
        self._clock = clock
        self._index = 0

    def __len__(self) -> int:
        return len(self.accounts)

    @property
    def current(self) -> Account:
        return self.accounts[self._index]

    def reset_current(self) -> None:
        """A clean check — clear the block streak for the active account."""
        self.current.consecutive_blocks = 0

    def set_cooldown(self, seconds: float) -> None:
        """Park the active account for `seconds` (it won't be reused until
        then). The caller manages the consecutive_blocks streak so the
        progressive backoff stays visible at the call site."""
        self.current.cooldown_until = self._clock() + seconds

    def rotate(self) -> Account | None:
        """Move to the next available account (checking others before wrapping
        back to the current one). Returns the new current account, or None if
        every account is still cooling down."""
        now = self._clock()
        n = len(self.accounts)
        for offset in range(1, n + 1):
            idx = (self._index + offset) % n
            if self.accounts[idx].is_available(now):
                self._index = idx
                return self.current
        return None

    def time_until_available(self) -> float:
        """Seconds until the soonest account frees up (0 if one is free now)."""
        now = self._clock()
        soonest = min(acc.cooldown_until for acc in self.accounts)
        return max(0.0, soonest - now)


def _state_path_for(base: str, label: str) -> str:
    """Derives a per-account storage-state filename from the base path, e.g.
    storage_state.json + "a@x.com" -> storage_state_a_x_com.json."""
    p = Path(base)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower() or "account"
    return str(p.with_name(f"{p.stem}_{safe}{p.suffix}"))


def resolve_accounts(config: AppConfig) -> list[Account]:
    """Builds the runtime account list. Uses config.accounts when present;
    otherwise falls back to a single account assembled from the legacy
    VFS_EMAIL/VFS_PASSWORD + global IMAP settings (keeping the original
    storage_state.json so existing setups are unaffected)."""
    base = config.vfs.storage_state_path

    if config.accounts:
        result: list[Account] = []
        for i, ac in enumerate(config.accounts):
            label = ac.label or ac.vfs_email or f"account{i + 1}"
            result.append(
                Account(config=ac, storage_state_path=_state_path_for(base, label))
            )
        logger.info("Loaded %d account(s) from config", len(result))
        return result

    single = AccountConfig(
        vfs_email=config.vfs.email,
        vfs_password=config.vfs.password,
        imap_username=config.imap.username,
        imap_password=config.imap.password,
        label=config.vfs.email,
    )
    return [Account(config=single, storage_state_path=base)]


def make_mailbox(config: AppConfig, account: Account) -> OTPMailbox:
    """Builds an OTPMailbox for `account`, overriding the global IMAP login
    with the account's own mailbox credentials when it has them."""
    imap = dataclasses.replace(
        config.imap,
        username=account.config.imap_username or config.imap.username,
        password=account.config.imap_password or config.imap.password,
    )
    return OTPMailbox(imap)
