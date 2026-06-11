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
    poll_interval_min_seconds: int = 120
    poll_interval_max_seconds: int = 300
    headless: bool = False
    storage_state_path: str = "storage_state.json"
    email: str = field(default_factory=lambda: os.environ.get("VFS_EMAIL", ""))
    password: str = field(default_factory=lambda: os.environ.get("VFS_PASSWORD", ""))


@dataclass
class IMAPConfig:
    host: str
    port: int = 993
    folder: str = "INBOX"
    sender_filter: str = "vfsglobal"
    otp_regex: str = r"\b(\d{4,8})\b"
    poll_timeout_seconds: int = 120
    poll_interval_seconds: int = 5
    username: str = field(default_factory=lambda: os.environ.get("IMAP_USERNAME", ""))
    password: str = field(default_factory=lambda: os.environ.get("IMAP_PASSWORD", ""))


@dataclass
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))


@dataclass
class AppConfig:
    vfs: VFSConfig
    imap: IMAPConfig
    telegram: TelegramConfig


def load_config(path: str = "config.yaml") -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return AppConfig(
        vfs=VFSConfig(**raw["vfs"]),
        imap=IMAPConfig(**raw["imap"]),
        telegram=TelegramConfig(**(raw.get("telegram") or {})),
    )
