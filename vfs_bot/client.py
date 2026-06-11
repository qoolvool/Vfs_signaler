import logging
import re
import time

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .config import AppConfig
from .human import human_click, human_type, random_delay
from .mailbox import OTPMailbox

logger = logging.getLogger(__name__)

NO_SLOTS_TEXT = "no appointment slots are currently available"
CLOUDFLARE_SUCCESS_TEXT = "Success"


class VFSClient:
    """Drives the VFS Global Croatia (Belgrade) appointment booking site."""

    def __init__(self, page: Page, config: AppConfig, mailbox: OTPMailbox):
        self.page = page
        self.config = config
        self.mailbox = mailbox

    # ------------------------------------------------------------------
    # Session state
    # ------------------------------------------------------------------
    def open_appointments(self) -> bool:
        """Navigates to the appointment page. Returns True if logged in,
        False if the site redirected to the login page."""
        self.page.goto(self.config.vfs.appointment_url, wait_until="domcontentloaded")
        try:
            self.page.wait_for_url(re.compile(r"/login", re.I), timeout=5000)
            return False
        except PlaywrightTimeoutError:
            return bool(re.search(r"/login", self.page.url, re.I)) is False

    # ------------------------------------------------------------------
    # Login + OTP flow
    # ------------------------------------------------------------------
    def login(self) -> None:
        page = self.page
        vfs = self.config.vfs

        page.goto(vfs.login_url, wait_until="domcontentloaded")
        random_delay(500, 1500)

        self._type_first(
            [
                lambda: page.get_by_label("Email", exact=False),
                lambda: page.get_by_placeholder(re.compile("email", re.I)),
                lambda: page.locator("input[type='email']"),
            ],
            vfs.email,
        )
        random_delay(200, 700)
        self._type_first(
            [
                lambda: page.get_by_label("Password", exact=False),
                lambda: page.locator("input[type='password']"),
            ],
            vfs.password,
        )

        self._wait_for_cloudflare()

        request_time = time.time()
        self._click_first(
            [
                lambda: page.get_by_role("button", name=re.compile("sign in", re.I)),
            ]
        )

        otp_input = self._first_visible(
            [
                lambda: page.get_by_label(re.compile("one time password", re.I)),
                lambda: page.get_by_placeholder(re.compile("OTP", re.I)),
            ],
            timeout=60000,
        )

        otp = self.mailbox.wait_for_otp(after_timestamp=request_time)
        random_delay(400, 1200)
        human_type(otp_input, otp)

        self._wait_for_cloudflare()

        self._click_first(
            [
                lambda: page.get_by_role("button", name=re.compile("sign in", re.I)),
            ]
        )

        page.wait_for_url(re.compile(r"book-appointment", re.I), timeout=60000)

    def _wait_for_cloudflare(self, timeout: int = 120000) -> None:
        try:
            self.page.get_by_text(CLOUDFLARE_SUCCESS_TEXT, exact=False).first.wait_for(
                state="visible", timeout=timeout
            )
        except PlaywrightTimeoutError:
            logger.warning("Cloudflare 'Success' indicator was not seen, continuing anyway")

    # ------------------------------------------------------------------
    # Appointment availability
    # ------------------------------------------------------------------
    def has_available_slot(self) -> bool:
        """Checks the already-open appointment page for available slots.
        Assumes `open_appointments()` was called first."""
        page = self.page
        vfs = self.config.vfs

        self._select_dropdown("Choose your Application Centre", vfs.application_centre)
        random_delay(300, 800)
        self._select_dropdown("Choose your appointment category", vfs.category)
        random_delay(300, 800)
        self._select_dropdown("Choose your sub-category", vfs.sub_category)

        page.wait_for_timeout(2000)

        no_slots = page.get_by_text(NO_SLOTS_TEXT, exact=False)
        return no_slots.count() == 0

    def _select_dropdown(self, label_text: str, value: str) -> None:
        page = self.page

        try:
            page.get_by_label(label_text, exact=False).select_option(label=value)
            page.wait_for_timeout(500)
            return
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass

        # Fallback: find the label/heading text and look for the nearest <select>
        # in the same form group.
        label = page.get_by_text(label_text, exact=False).first
        container = label.locator(
            "xpath=ancestor::*[self::div or self::section or self::form][1]"
        )
        container.locator("select").first.select_option(label=value)
        page.wait_for_timeout(500)

    # ------------------------------------------------------------------
    # Locator helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _first_visible(candidates, timeout: int = 30000) -> Locator:
        last_error: Exception | None = None
        per_candidate_timeout = max(timeout // len(candidates), 1000)
        for candidate in candidates:
            locator = candidate().first
            try:
                locator.wait_for(state="visible", timeout=per_candidate_timeout)
                return locator
            except PlaywrightTimeoutError as exc:
                last_error = exc
        raise last_error or PlaywrightTimeoutError("No matching element found")

    def _type_first(self, candidates, value: str) -> None:
        locator = self._first_visible(candidates)
        human_type(locator, value)

    def _click_first(self, candidates) -> None:
        locator = self._first_visible(candidates)
        human_click(self.page, locator)

    def save_debug_screenshot(self, name: str) -> str | None:
        path = f"debug_{name}_{int(time.time())}.png"
        try:
            self.page.screenshot(path=path, full_page=True)
            return path
        except Exception:
            logger.exception("Failed to capture debug screenshot")
            return None
