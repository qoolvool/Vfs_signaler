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
    poll_interval_min_seconds: int = 1800
    poll_interval_max_seconds: int = 2400
    reminder_interval_seconds: int = 0
    # How long to back off after VFS returns a hard block (e.g. 429002
    # "Unauthorised Activity"). Hammering it only extends the block.
    access_denied_backoff_seconds: int = 1800
    headless: bool = False
    storage_state_path: str = "storage_state.json"
    debug_screenshots: bool = True
    debug_dir: str = "debug"
    email: str = field(default_factory=lambda: os.environ.get("VFS_EMAIL", ""))
    password: str = field(default_factory=lambda: os.environ.get("VFS_PASSWORD", ""))


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


def load_config(path: str = "config.yaml") -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return AppConfig(
        vfs=VFSConfig(**raw["vfs"]),
        imap=IMAPConfig(**raw["imap"]),
        telegram=TelegramConfig(**(raw.get("telegram") or {})),
        proxy=ProxyConfig(**(raw.get("proxy") or {})),
    )
