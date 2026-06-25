import logging
import random
import sys
import threading
import time
import traceback

from .browser import BrowserSession
from .client import (
    AccessDeniedError,
    AccessRestrictedError,
    AccountLockedError,
    RequestTimedOutError,
    SessionExpiredError,
    VFSClient,
)
from .config import AppConfig, load_config
from .mailbox import OTPMailbox
from .notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _interruptible_sleep(seconds: float, stop_event: threading.Event | None) -> bool:
    """Sleep for *seconds*, returning early if *stop_event* is set.
    Returns True when interrupted (caller should break out of the loop)."""
    if stop_event:
        return stop_event.wait(seconds)
    time.sleep(seconds)
    return False


def run(
    config_path: str = "config.yaml",
    *,
    config: AppConfig | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    if config is None:
        config = load_config(config_path)
    mailbox = OTPMailbox(config.imap)
    notifier = TelegramNotifier(config.telegram)
    notifier.start_polling(stop_event)

    with BrowserSession(config.vfs, config.proxy) as session:
        page = session.new_page()
        client = VFSClient(page, config, mailbox, notifier)

        notifier.send("VFS bot: мониторинг слотов запущен.")

        # Counts blocks that happen back-to-back. Each consecutive block makes
        # the recoverable backoffs (Access Denied / 504) progressively longer so
        # the bot stops hammering VFS when it's clearly being rate-limited. A
        # clean check resets it to 0.
        consecutive_blocks = 0
        while not (stop_event and stop_event.is_set()):
            try:
                if not client.open_appointments():
                    notifier.send("VFS bot: требуется вход в аккаунт...")
                    try:
                        client.login()
                    except (
                        AccessDeniedError,
                        RequestTimedOutError,
                        AccountLockedError,
                        SessionExpiredError,
                        AccessRestrictedError,
                    ):
                        raise
                    except Exception as exc:
                        logger.exception("Login failed")
                        shot = client.save_debug_screenshot("login_failed")
                        tb = traceback.format_exc()
                        err_msg = (
                            f"VFS bot: вход не удался.\n\n"
                            f"Ошибка: {type(exc).__name__}: {exc}\n\n"
                            f"Traceback:\n{tb[-1500:]}"
                        )
                        if shot:
                            notifier.send_photo(shot, err_msg)
                        else:
                            notifier.send(err_msg)
                        raise
                    shot = client.save_debug_screenshot("login_success")
                    if shot:
                        notifier.send_photo(shot, "VFS bot: вход выполнен успешно.")
                    else:
                        notifier.send("VFS bot: вход выполнен успешно.")
                    # login() already navigated to the appointment form
                    # (clicked "Start New Booking" and waited for it).
                    # Do NOT call open_appointments() here — that would
                    # reload the page and lose the session.
                    session.save_state()

                # has_available_slot() itself sends the per-check result to
                # Telegram (page text + screenshot, or a warning if no text),
                # so we don't post any extra slot messages here.
                client.has_available_slot()
                # We reached and read the appointment page without a block, so
                # whatever rate-limit streak we had is over.
                consecutive_blocks = 0
            except AccessDeniedError:
                logger.exception("VFS/Cloudflare returned an access-denied page")
                shot = client.save_debug_screenshot("access_denied")
                session.clear_state()
                client.navigate_to_login()

                consecutive_blocks += 1
                # Progressive backoff: 1x, 2x, 3x... the base, capped at 4x.
                backoff = config.vfs.access_denied_backoff_seconds * min(
                    consecutive_blocks, 4
                )
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
                if _interruptible_sleep(backoff, stop_event):
                    break
                continue
            except AccountLockedError:
                logger.exception("VFS returned an Account Locked (429202) page")
                shot = client.save_debug_screenshot("account_locked")
                session.clear_state()
                client.navigate_to_login()

                consecutive_blocks += 1
                backoff = 7200
                msg = (
                    "VFS bot: аккаунт временно заблокирован (Account Locked / "
                    "429202). Куки сброшены, жду 2 часа и пробую снова."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                logger.info("Backing off for %d seconds after account-locked", backoff)
                if _interruptible_sleep(backoff, stop_event):
                    break
                continue
            except AccessRestrictedError:
                logger.exception("VFS returned an Access Restricted (429001) page")
                shot = client.save_debug_screenshot("access_restricted")
                session.clear_state()
                client.navigate_to_login()

                consecutive_blocks += 1
                backoff = 7200
                msg = (
                    "VFS bot: доступ к user ID ограничен (Access Restricted / "
                    "429001 — необычная активность). Куки сброшены, жду 2 часа "
                    "и пробую снова."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                logger.info(
                    "Backing off for %d seconds after access-restricted", backoff
                )
                if _interruptible_sleep(backoff, stop_event):
                    break
                continue
            except SessionExpiredError:
                logger.exception("VFS returned a Session Expired page")
                shot = client.save_debug_screenshot("session_expired")
                session.clear_state()
                client.navigate_to_login()

                consecutive_blocks += 1
                msg = (
                    "VFS bot: сессия истекла (Session Expired or Invalid). "
                    "Куки сброшены, пробую войти заново."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                logger.info("Session expired — cleared state, retrying immediately")
                continue
            except RequestTimedOutError:
                logger.exception("VFS returned a Request Timed Out (504) page")
                shot = client.save_debug_screenshot("request_timed_out")
                client.navigate_to_login()

                consecutive_blocks += 1
                backoff = 600 * min(consecutive_blocks, 3)
                msg = (
                    "VFS bot: сайт ответил 'Request Timed Out (504)'. "
                    "Прерываю текущую попытку, жду 10 минут и пробую снова."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                logger.info("Backing off for %d seconds after a 504 timeout", backoff)
                if _interruptible_sleep(backoff, stop_event):
                    break
                continue
            except Exception as exc:
                logger.exception("Error during polling cycle")
                shot = client.save_debug_screenshot("error")
                client.navigate_to_login()
                tb = traceback.format_exc()
                err_msg = (
                    f"VFS bot: произошла ошибка.\n\n"
                    f"Ошибка: {type(exc).__name__}: {exc}\n\n"
                    f"Traceback:\n{tb[-1500:]}"
                )
                if shot:
                    notifier.send_photo(shot, err_msg)
                else:
                    notifier.send(err_msg)

            session.save_state()

            delay = random.randint(
                config.vfs.poll_interval_min_seconds,
                config.vfs.poll_interval_max_seconds,
            )
            logger.info("Next check in %d seconds", delay)
            if _interruptible_sleep(delay, stop_event):
                break

        logger.info("Bot stopped")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
