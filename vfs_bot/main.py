import logging
import random
import sys
import threading
import time
import traceback

from .accounts import Account, AccountPool, make_mailbox, resolve_accounts
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

    pool = AccountPool(resolve_accounts(config))
    notifier = TelegramNotifier(config.telegram)
    notifier.start_polling(stop_event)

    session = BrowserSession(config.vfs, config.proxy)
    # Start on the first account's own cookie store.
    session.storage_state_path = pool.current.storage_state_path

    with session:
        page = session.new_page()
        mailbox = make_mailbox(config, pool.current)
        client = VFSClient(page, config, mailbox, notifier, account=pool.current.config)

        def activate(account: Account) -> None:
            """Repoints the browser session, mailbox and client at `account`.
            Used after a block to continue under a different login."""
            nonlocal page, mailbox, client
            session.switch_storage_state(account.storage_state_path, save_current=False)
            page = session.new_page()
            mailbox = make_mailbox(config, account)
            client = VFSClient(page, config, mailbox, notifier, account=account.config)

        def switch_or_wait() -> bool:
            """After the active account was parked, rotate to another available
            account, or sleep until the soonest one frees up and then use it.
            Returns True if interrupted by stop_event (caller should break)."""
            target = pool.rotate()
            if target is None:
                wait = pool.time_until_available()
                minutes = max(int(wait // 60), 1)
                if len(pool) > 1:
                    notifier.send(
                        f"VFS bot: все {len(pool)} аккаунта на паузе. "
                        f"Жду ~{minutes} мин до ближайшего свободного."
                    )
                else:
                    notifier.send(f"VFS bot: пауза ~{minutes} мин перед повтором.")
                logger.info("All accounts cooling down, waiting %d seconds", wait)
                if _interruptible_sleep(wait, stop_event):
                    return True
                target = pool.rotate()
                if target is None:
                    return False
            if len(pool) > 1:
                notifier.send(f"VFS bot: переключаюсь на аккаунт {target.label}.")
            activate(target)
            return False

        if len(pool) > 1:
            notifier.send(
                f"VFS bot: мониторинг слотов запущен. Аккаунтов: {len(pool)}."
            )
        else:
            notifier.send("VFS bot: мониторинг слотов запущен.")

        while not (stop_event and stop_event.is_set()):
            try:
                if not client.open_appointments():
                    notifier.send(
                        f"VFS bot: требуется вход в аккаунт {pool.current.label}..."
                    )
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
                            f"VFS bot: вход не удался ({pool.current.label}).\n\n"
                            f"Ошибка: {type(exc).__name__}: {exc}\n\n"
                            f"Traceback:\n{tb[-1500:]}"
                        )
                        if shot:
                            notifier.send_photo(shot, err_msg)
                        else:
                            notifier.send(err_msg)
                        raise
                    shot = client.save_debug_screenshot("login_success")
                    success_msg = f"VFS bot: вход выполнен ({pool.current.label})."
                    if shot:
                        notifier.send_photo(shot, success_msg)
                    else:
                        notifier.send(success_msg)
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
                # this account's rate-limit streak is over.
                pool.reset_current()
            except AccessDeniedError:
                logger.exception("VFS/Cloudflare returned an access-denied page")
                shot = client.save_debug_screenshot("access_denied")
                acc = pool.current
                acc.consecutive_blocks += 1
                # Progressive backoff: 1x, 2x, 3x... the base, capped at 4x.
                backoff = config.vfs.access_denied_backoff_seconds * min(
                    acc.consecutive_blocks, 4
                )
                pool.set_cooldown(backoff)
                session.clear_state()
                minutes = max(backoff // 60, 1)
                msg = (
                    f"VFS bot: аккаунт {acc.label} заблокирован (Access Denied / "
                    f"429002 — слишком много запросов). Ставлю на паузу ~{minutes} "
                    "мин."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                if switch_or_wait():
                    break
                continue
            except AccountLockedError:
                logger.exception("VFS returned an Account Locked (429202) page")
                shot = client.save_debug_screenshot("account_locked")
                acc = pool.current
                acc.consecutive_blocks += 1
                backoff = 7200
                pool.set_cooldown(backoff)
                session.clear_state()
                msg = (
                    f"VFS bot: аккаунт {acc.label} временно заблокирован (Account "
                    "Locked / 429202). Ставлю на паузу 2 часа."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                if switch_or_wait():
                    break
                continue
            except AccessRestrictedError:
                logger.exception("VFS returned an Access Restricted (429001) page")
                shot = client.save_debug_screenshot("access_restricted")
                acc = pool.current
                acc.consecutive_blocks += 1
                backoff = 7200
                pool.set_cooldown(backoff)
                session.clear_state()
                msg = (
                    f"VFS bot: доступ к аккаунту {acc.label} ограничен (Access "
                    "Restricted / 429001 — необычная активность). Ставлю на паузу "
                    "2 часа."
                )
                if shot:
                    notifier.send_photo(shot, msg)
                else:
                    notifier.send(msg)
                if switch_or_wait():
                    break
                continue
            except SessionExpiredError:
                logger.exception("VFS returned a Session Expired page")
                shot = client.save_debug_screenshot("session_expired")
                session.clear_state()
                client.navigate_to_login()

                msg = (
                    f"VFS bot: сессия истекла ({pool.current.label}). "
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

                acc = pool.current
                acc.consecutive_blocks += 1
                backoff = 600 * min(acc.consecutive_blocks, 3)
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
