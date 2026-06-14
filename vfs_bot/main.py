import logging
import random
import sys
import time

from .browser import BrowserSession
from .client import AccessDeniedError, AccountLockedError, RequestTimedOutError, VFSClient
from .config import load_config
from .mailbox import OTPMailbox
from .notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(config_path: str = "config.yaml") -> None:
    config = load_config(config_path)
    mailbox = OTPMailbox(config.imap)
    notifier = TelegramNotifier(config.telegram)

    with BrowserSession(config.vfs, config.proxy) as session:
        page = session.new_page()
        client = VFSClient(page, config, mailbox)

        notifier.send("VFS bot: мониторинг слотов запущен.")

        already_notified = False
        last_notified_at: float | None = None
        while True:
            try:
                if not client.open_appointments():
                    notifier.send("VFS bot: требуется вход в аккаунт...")
                    try:
                        client.login()
                    except (AccessDeniedError, RequestTimedOutError, AccountLockedError):
                        raise
                    except Exception:
                        logger.exception("Login failed")
                        shot = client.save_debug_screenshot("login_failed")
                        if shot:
                            notifier.send_photo(shot, "VFS bot: вход не удался, см. скриншот")
                        else:
                            notifier.send("VFS bot: вход не удался, см. логи.")
                        raise
                    shot = client.save_debug_screenshot("login_success")
                    if shot:
                        notifier.send_photo(shot, "VFS bot: вход выполнен успешно.")
                    else:
                        notifier.send("VFS bot: вход выполнен успешно.")
                    client.open_appointments()
                    session.save_state()

                if client.has_available_slot():
                    reminder = config.vfs.reminder_interval_seconds
                    due_for_reminder = (
                        already_notified
                        and reminder > 0
                        and last_notified_at is not None
                        and time.time() - last_notified_at >= reminder
                    )
                    if not already_notified or due_for_reminder:
                        notifier.send(
                            "VFS bot: появился свободный слот для "
                            f"{config.vfs.application_centre} / "
                            f"{config.vfs.category} / {config.vfs.sub_category}! "
                            "Зайдите на сайт и забронируйте его."
                        )
                        already_notified = True
                        last_notified_at = time.time()
                else:
                    if already_notified:
                        notifier.send(
                            "VFS bot: слот для "
                            f"{config.vfs.application_centre} / "
                            f"{config.vfs.category} / {config.vfs.sub_category} "
                            "больше недоступен."
                        )
                    already_notified = False
                    last_notified_at = None
            except AccessDeniedError:
                logger.exception("VFS/Cloudflare returned an access-denied page")
                shot = client.save_debug_screenshot("access_denied")
                session.clear_state()
                already_notified = False
                last_notified_at = None
                backoff = config.vfs.access_denied_backoff_seconds
                minutes = max(backoff // 60, 1)
                msg = (
                    "VFS bot: доступ заблокирован (Access Denied / 429002 — "
                    "слишком много запросов). Сессия сброшена, делаю длинную "
                    f"паузу ~{minutes} мин, чтобы не усугублять блокировку."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                logger.info("Backing off for %d seconds after access-denied", backoff)
                time.sleep(backoff)
                continue
            except AccountLockedError:
                logger.exception("VFS returned an Account Locked (429202) page")
                shot = client.save_debug_screenshot("account_locked")
                session.clear_state()
                already_notified = False
                last_notified_at = None
                backoff = 1800
                msg = (
                    "VFS bot: аккаунт временно заблокирован (Account Locked / "
                    "429202). Куки сброшены, жду 30 минут и пробую снова."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                logger.info("Backing off for %d seconds after account-locked", backoff)
                time.sleep(backoff)
                continue
            except RequestTimedOutError:
                logger.exception("VFS returned a Request Timed Out (504) page")
                shot = client.save_debug_screenshot("request_timed_out")
                already_notified = False
                last_notified_at = None
                backoff = 600
                msg = (
                    "VFS bot: сайт ответил 'Request Timed Out (504)'. "
                    "Прерываю текущую попытку, жду 10 минут и пробую снова."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                logger.info("Backing off for %d seconds after a 504 timeout", backoff)
                time.sleep(backoff)
                continue
            except Exception:
                logger.exception("Error during polling cycle")
                shot = client.save_debug_screenshot("error")
                if shot:
                    notifier.send_photo(shot, "VFS bot: произошла ошибка, см. логи.")
                else:
                    notifier.send("VFS bot: произошла ошибка, см. логи.")

            session.save_state()

            delay = random.randint(
                config.vfs.poll_interval_min_seconds,
                config.vfs.poll_interval_max_seconds,
            )
            logger.info("Next check in %d seconds", delay)
            time.sleep(delay)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
