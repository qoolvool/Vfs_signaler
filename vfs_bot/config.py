import os
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class VFSConfig:
    login_url: str
    appointment_url: str
    application_centre: str
    category: str
    sub_category: str
    centre_keyword: str = ""
    category_keyword: str = ""
    sub_category_keyword: str = ""
    poll_interval_min_seconds: int = 1800
    poll_interval_max_seconds: int = 2400
    reminder_interval_seconds: int = 0
    access_denied_backoff_seconds: int = 7500
    headless: bool = False
    storage_state_path: str = "storage_state.json"
    debug_screenshots: bool = True
    debug_dir: str = "debug"
    email: str = field(default_factory=lambda: os.environ.get("VFS_EMAIL", ""))
    password: str = field(default_factory=lambda: os.environ.get("VFS_PASSWORD", ""))

    def __post_init__(self) -> None:
        if not self.centre_keyword:
            self.centre_keyword = self.application_centre.lower()
        if not self.category_keyword:
            self.category_keyword = self.category.lower()
        if not self.sub_category_keyword:
            self.sub_category_keyword = self.sub_category.lower()


@dataclass
class IMAPConfig:
    host: str
    port: int = 993
    folder: str = "INBOX"
    sender_filter: str = "vfshelpline.com"
    otp_regex: str = r"\b(\d{4,8})\b"
    poll_timeout_seconds: int = 120
    poll_interval_seconds: int = 5
    username: str = field(default_factory=lambda: os.environ.get("IMAP_USERNAME", ""))
    password: str = field(default_factory=lambda: os.environ.get("IMAP_PASSWORD", ""))


@dataclass
class TelegramConfig:
    bot_token: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )
    chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))

    @property
    def chat_ids(self) -> list[str]:
        """Returns a list of chat IDs, splitting comma-separated values."""
        if not self.chat_id:
            return []
        return [cid.strip() for cid in self.chat_id.split(",") if cid.strip()]


@dataclass
class AccountConfig:
    """A single VFS Global login. Each account is registered to its own email,
    so each may point at a different OTP mailbox. imap_username/imap_password
    fall back to the global IMAP config when left empty."""

    vfs_email: str
    vfs_password: str
    imap_username: str = ""
    imap_password: str = ""
    label: str = ""


@dataclass
class ProxyConfig:
    # e.g. "http://host:port" or "socks5://host:port". Empty disables the proxy.
    server: str = field(default_factory=lambda: os.environ.get("PROXY_SERVER", ""))
    username: str = field(default_factory=lambda: os.environ.get("PROXY_USERNAME", ""))
    password: str = field(default_factory=lambda: os.environ.get("PROXY_PASSWORD", ""))

    @property
    def enabled(self) -> bool:
        return bool(self.server)

    def to_playwright(self) -> dict | None:
        if not self.enabled:
            return None
        proxy: dict = {"server": self.server}
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy


@dataclass
class AppConfig:
    vfs: VFSConfig
    imap: IMAPConfig
    telegram: TelegramConfig
    proxy: ProxyConfig
    accounts: list[AccountConfig] = field(default_factory=list)


def load_config(path: str = "config.yaml") -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    accounts = [AccountConfig(**a) for a in (raw.get("accounts") or [])]

    return AppConfig(
        vfs=VFSConfig(**raw["vfs"]),
        imap=IMAPConfig(**raw["imap"]),
        telegram=TelegramConfig(**(raw.get("telegram") or {})),
        proxy=ProxyConfig(**(raw.get("proxy") or {})),
        accounts=accounts,
    )
