import logging

import requests

from .config import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, config: TelegramConfig):
        self.config = config

    def send(self, text: str) -> None:
        logger.info(text)

        if not self.config.bot_token or not self.config.chat_id:
            return

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        try:
            requests.post(
                url,
                data={"chat_id": self.config.chat_id, "text": text},
                timeout=15,
            )
        except requests.RequestException:
            logger.exception("Failed to send Telegram notification")
