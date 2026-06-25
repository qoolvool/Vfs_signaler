import logging

import requests

from .config import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, config: TelegramConfig):
        self.config = config

    def send(self, text: str) -> None:
        logger.info(text)

        if not self.config.bot_token or not self.config.chat_ids:
            return

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        for chat_id in self.config.chat_ids:
            try:
                requests.post(
                    url,
                    data={"chat_id": chat_id, "text": text},
                    timeout=15,
                )
            except requests.RequestException:
                logger.exception(
                    "Failed to send Telegram notification to %s", chat_id
                )

    def send_photo(self, path: str, caption: str = "") -> None:
        logger.info("[screenshot] %s %s", path, caption)

        if not self.config.bot_token or not self.config.chat_ids:
            return

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendPhoto"
        for chat_id in self.config.chat_ids:
            try:
                with open(path, "rb") as photo:
                    requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption},
                        files={"photo": photo},
                        timeout=30,
                    )
            except (requests.RequestException, OSError):
                logger.exception(
                    "Failed to send Telegram screenshot to %s", chat_id
                )
