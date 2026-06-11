import logging
import random
import sys
import time

from .browser import BrowserSession
from .client import VFSClient
from .config import load_config
from .mailbox import OTPMailbox
from .notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(config_path: str = "config.yaml") -> None:
    config = load_config(config_path)
    mailbox = OTPMailbox(config.imap)
    notifier = TelegramNotifier(config.telegram)

    with BrowserSession(config.vfs) as session:
        page = session.new_page()
        client = VFSClient(page, config, mailbox)

        notifier.send("VFS bot: мониторинг слотов запущен.")

        already_notified = False
        while True:
            try:
                if not client.open_appointments():
                    notifier.send("VFS bot: требуется вход в аккаунт...")
                    client.login()
                    notifier.send("VFS bot: вход выполнен успешно.")
                    client.open_appointments()

                if client.has_available_slot():
                    if not already_notified:
                        notifier.send(
                            "VFS bot: появился свободный слот для "
                            f"{config.vfs.application_centre} / "
                            f"{config.vfs.category} / {config.vfs.sub_category}! "
                            "Зайдите на сайт и забронируйте его."
                        )
                        already_notified = True
                else:
                    already_notified = False
            except Exception:
                logger.exception("Error during polling cycle")
                shot = client.save_debug_screenshot("error")
                suffix = f" (скриншот: {shot})" if shot else ""
                notifier.send(f"VFS bot: произошла ошибка, см. логи.{suffix}")

            delay = random.randint(
                config.vfs.poll_interval_min_seconds,
                config.vfs.poll_interval_max_seconds,
            )
            logger.info("Next check in %d seconds", delay)
            time.sleep(delay)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
