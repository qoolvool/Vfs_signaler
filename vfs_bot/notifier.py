import json
import logging
import threading
from pathlib import Path

import requests

from .config import TelegramConfig

logger = logging.getLogger(__name__)

SUBSCRIBERS_FILE = "subscribers.json"


class TelegramNotifier:
    def __init__(self, config: TelegramConfig):
        self.config = config
        self._subscribers: set[str] = set()
        self._lock = threading.Lock()
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._offset = 0

        self._load_subscribers()
        for cid in config.chat_ids:
            self._subscribers.add(cid)

    def _subscribers_path(self) -> Path:
        return Path(SUBSCRIBERS_FILE)

    def _load_subscribers(self) -> None:
        path = self._subscribers_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            with self._lock:
                self._subscribers = {str(cid) for cid in data}
            logger.info("Loaded %d subscriber(s) from %s", len(self._subscribers), path)
        except Exception:
            logger.exception("Failed to load subscribers from %s", path)

    def _save_subscribers(self) -> None:
        path = self._subscribers_path()
        try:
            with self._lock:
                data = sorted(self._subscribers)
            path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            logger.exception("Failed to save subscribers to %s", path)

    @property
    def chat_ids(self) -> list[str]:
        with self._lock:
            return list(self._subscribers)

    def start_polling(self, stop_event: threading.Event | None = None) -> None:
        if not self.config.bot_token:
            return
        if stop_event:
            self._stop_event = stop_event
        self._poll_thread = threading.Thread(
            target=self._poll_updates, daemon=True, name="tg-poller"
        )
        self._poll_thread.start()
        logger.info("Telegram update poller started")

    def _poll_updates(self) -> None:
        while not self._stop_event.is_set():
            try:
                url = f"https://api.telegram.org/bot{self.config.bot_token}/getUpdates"
                resp = requests.get(
                    url,
                    params={"offset": self._offset, "timeout": 30},
                    timeout=35,
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.warning("Telegram getUpdates error: %s", data)
                    self._stop_event.wait(5)
                    continue

                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    message = update.get("message") or {}
                    text = (message.get("text") or "").strip().lower()
                    chat = message.get("chat", {})
                    chat_id = str(chat.get("id", ""))
                    name = (
                        chat.get("first_name", "")
                        or chat.get("username", "")
                        or chat_id
                    )

                    if not chat_id:
                        continue

                    if text == "/start":
                        with self._lock:
                            self._subscribers.add(chat_id)
                        self._save_subscribers()
                        logger.info("New subscriber: %s (%s)", name, chat_id)
                        self._send_to(
                            chat_id,
                            "VFS bot: вы подписаны на уведомления о слотах. "
                            "Отправьте /stop чтобы отписаться.",
                        )
                    elif text == "/stop":
                        with self._lock:
                            self._subscribers.discard(chat_id)
                        self._save_subscribers()
                        logger.info("Unsubscribed: %s (%s)", name, chat_id)
                        self._send_to(chat_id, "VFS bot: вы отписаны от уведомлений.")
                    elif text == "/status":
                        count = len(self.chat_ids)
                        self._send_to(
                            chat_id,
                            f"VFS bot: бот работает. Подписчиков: {count}.",
                        )

            except requests.RequestException:
                logger.exception("Telegram polling error")
                self._stop_event.wait(5)
            except Exception:
                logger.exception("Unexpected error in Telegram poller")
                self._stop_event.wait(10)

    def _send_to(self, chat_id: str, text: str) -> None:
        if not self.config.bot_token:
            return
        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        try:
            requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
        except requests.RequestException:
            logger.exception("Failed to send message to %s", chat_id)

    def send(self, text: str) -> None:
        logger.info(text)

        if not self.config.bot_token:
            return

        recipients = self.chat_ids
        if not recipients:
            return

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        for chat_id in recipients:
            try:
                requests.post(
                    url,
                    data={"chat_id": chat_id, "text": text},
                    timeout=15,
                )
            except requests.RequestException:
                logger.exception("Failed to send Telegram notification to %s", chat_id)

    def send_photo(self, path: str, caption: str = "") -> None:
        logger.info("[screenshot] %s %s", path, caption)

        if not self.config.bot_token:
            return

        recipients = self.chat_ids
        if not recipients:
            return

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendPhoto"
        for chat_id in recipients:
            try:
                with open(path, "rb") as photo:
                    requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption},
                        files={"photo": photo},
                        timeout=30,
                    )
            except (requests.RequestException, OSError):
                logger.exception("Failed to send Telegram screenshot to %s", chat_id)
