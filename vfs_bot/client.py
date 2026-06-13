import logging
import random
import re
import time
from pathlib import Path

from patchright.sync_api import Locator, Page
from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

from .config import AppConfig
from .human import (
    human_click,
    human_mouse_move_to,
    human_mouse_wander,
    human_type,
    random_delay,
)
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
        self._accept_cookies()
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

        logger.info("Login step: opening login page %s", vfs.login_url)
        page.goto(vfs.login_url, wait_until="domcontentloaded")
        random_delay(500, 1500)
        self._accept_cookies()
        self._step_screenshot("01_login_page")

        logger.info("Login step: entering email")
        self._type_first(
            [
                lambda: page.get_by_label("Email", exact=False),
                lambda: page.get_by_placeholder(re.compile("email", re.I)),
                lambda: page.locator("input[type='email']"),
            ],
            vfs.email,
        )
        random_delay(200, 700)

        logger.info("Login step: entering password")
        self._type_first(
            [
                lambda: page.get_by_label("Password", exact=False),
                lambda: page.locator("input[type='password']"),
            ],
            vfs.password,
        )
        self._step_screenshot("02_credentials_filled")

        logger.info("Login step: waiting for Cloudflare on login page")
        self._wait_for_cloudflare()
        self._step_screenshot("03_cloudflare_login")

        logger.info("Login step: clicking Sign In (credentials)")
        request_time = time.time()
        self._click_sign_in()

        logger.info("Login step: waiting for OTP input field")
        otp_input = self._first_visible(
            [
                lambda: page.get_by_label(re.compile("one time password", re.I)),
                lambda: page.get_by_placeholder(re.compile("OTP", re.I)),
            ],
            timeout=60000,
        )
        self._step_screenshot("04_otp_page")

        logger.info("Login step: waiting for OTP email")
        otp = self.mailbox.wait_for_otp(after_timestamp=request_time)
        logger.info("Login step: OTP received, entering it")
        random_delay(400, 1200)
        human_type(otp_input, otp)
        self._step_screenshot("05_otp_entered")

        logger.info("Login step: waiting for Cloudflare on OTP page")
        self._wait_for_cloudflare()

        logger.info("Login step: clicking Sign In (OTP)")
        self._click_sign_in()

        logger.info("Login step: waiting for redirect to appointment page")
        page.wait_for_url(re.compile(r"book-appointment", re.I), timeout=60000)
        self._step_screenshot("06_login_success")
        logger.info("Login successful")

    def _click_sign_in(self) -> None:
        """Clicks the Sign In button, waiting for it to become enabled first.
        VFS often keeps this button disabled until the Cloudflare token has
        actually landed in a hidden field, which can lag behind the visible
        'Success' indicator by a second or two."""
        page = self.page
        locator = self._first_visible(
            [
                lambda: page.get_by_role("button", name=re.compile("sign in", re.I)),
                lambda: page.locator("button:has-text('Sign In')"),
                lambda: page.locator("input[type='submit']"),
                lambda: page.get_by_text(re.compile(r"^\s*sign in\s*$", re.I)),
            ]
        )

        deadline = time.time() + 15
        while time.time() < deadline and not locator.is_enabled():
            logger.info("Sign In button is disabled, waiting...")
            page.wait_for_timeout(500)

        if not locator.is_enabled():
            logger.warning("Sign In button still disabled after 15s, clicking anyway")

        human_click(page, locator)

    def _accept_cookies(self, timeout: int = 5000) -> None:
        """Dismisses the cookie consent banner if present. Best-effort and
        non-fatal: if no banner shows up within `timeout`, does nothing."""
        page = self.page
        try:
            button = page.get_by_role(
                "button", name=re.compile("accept", re.I)
            ).first
            button.wait_for(state="visible", timeout=timeout)
            human_click(page, button)
            logger.info("Dismissed cookie consent banner")
        except PlaywrightTimeoutError:
            pass
        except Exception:
            logger.exception("Failed to dismiss cookie consent banner")

    def _wait_for_cloudflare(self, timeout: int = 120000) -> None:
        page = self.page

        # Some idle, hand-like mouse drift before/around the challenge. This is
        # behavioural camouflage only; it does NOT help against a flagged IP or
        # browser fingerprint (the usual reason a challenge keeps failing).
        try:
            human_mouse_wander(page)
        except Exception:
            pass

        deadline = time.time() + timeout / 1000
        clicked = False
        click_after = time.time() + 6  # give it time to auto-pass first
        while time.time() < deadline:
            if self._cloudflare_passed():
                logger.info("Cloudflare challenge passed")
                return
            # If it hasn't auto-passed after a few seconds, try clicking the
            # checkbox once (some configs require an explicit interaction).
            if not clicked and time.time() > click_after:
                self._click_turnstile_checkbox()
                clicked = True
            page.wait_for_timeout(1000)

        logger.warning("Cloudflare pass not confirmed within timeout, continuing anyway")

    def _cloudflare_passed(self) -> bool:
        """Detects whether the Cloudflare/Turnstile challenge is solved.
        The reliable signal is the hidden token field in the *main* DOM;
        the visible 'Success' text lives inside a cross-origin iframe and is
        not reachable via page.get_by_text()."""
        page = self.page

        # 1) Hidden Turnstile/Recaptcha token populated => challenge solved.
        try:
            token = page.evaluate(
                "() => { const el = document.querySelector("
                "'[name=\"cf-turnstile-response\"], "
                "[name=\"g-recaptcha-response\"]'); "
                "return el ? el.value : ''; }"
            )
            if token:
                return True
        except Exception:
            pass

        # 2) "Success" text rendered inside the Turnstile iframe.
        try:
            frame = page.frame_locator(
                "iframe[src*='challenges.cloudflare.com'], "
                "iframe[title*='Cloudflare' i], iframe[title*='challenge' i]"
            )
            if frame.get_by_text(CLOUDFLARE_SUCCESS_TEXT, exact=False).first.is_visible():
                return True
        except Exception:
            pass

        # 3) Some flows render "Success" in the main document.
        try:
            if page.get_by_text(CLOUDFLARE_SUCCESS_TEXT, exact=False).first.is_visible():
                return True
        except Exception:
            pass

        return False

    def _click_turnstile_checkbox(self) -> None:
        """Best-effort: locate the Cloudflare Turnstile iframe, move the mouse
        onto its checkbox via a curved path, and click. Non-fatal on failure."""
        page = self.page
        try:
            frame = page.locator(
                "iframe[src*='challenges.cloudflare.com'], "
                "iframe[title*='Cloudflare' i], "
                "iframe[title*='challenge' i]"
            ).first
            frame.wait_for(state="visible", timeout=8000)
            box = frame.bounding_box()
            if not box:
                return
            # The checkbox sits near the left edge of the widget, vertically
            # centred. Aim for that area with a little jitter.
            x = box["x"] + min(box["width"] * 0.5, 30) + random.uniform(-4, 4)
            y = box["y"] + box["height"] * 0.5 + random.uniform(-4, 4)
            human_mouse_move_to(page, x, y)
            random_delay(150, 400)
            page.mouse.click(x, y)
            logger.info("Clicked Cloudflare Turnstile checkbox area")
            random_delay(400, 900)
        except PlaywrightTimeoutError:
            logger.info("No Cloudflare Turnstile iframe found to click")
        except Exception:
            logger.exception("Failed to click Cloudflare Turnstile checkbox")

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

        # 1) Native <select> associated with the label via aria/for
        try:
            page.get_by_label(label_text, exact=False).select_option(label=value)
            page.wait_for_timeout(500)
            return
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass

        # Find the label/heading text and its surrounding form group, used by
        # both remaining fallbacks below.
        label = page.get_by_text(label_text, exact=False).first
        container = label.locator(
            "xpath=ancestor::*[self::div or self::section or self::form][1]"
        )

        # 2) <select> in the same form group without a proper label association
        try:
            select = container.locator("select").first
            select.wait_for(state="attached", timeout=2000)
            select.select_option(label=value)
            page.wait_for_timeout(500)
            return
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass

        # 3) Custom (non-native) dropdown widget: click to open it, then
        # click the matching option from the list that appears.
        self._select_custom_dropdown(container, value)

    def _select_custom_dropdown(self, container: Locator, value: str) -> None:
        page = self.page

        trigger = container.locator(
            "[role='combobox'], [role='listbox'], "
            "input[readonly], .dropdown, .select, button"
        ).first
        trigger.wait_for(state="visible", timeout=5000)
        human_click(page, trigger)

        try:
            option = page.get_by_role("option", name=value, exact=False).first
            option.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            option = page.get_by_text(value, exact=False).last
            option.wait_for(state="visible", timeout=3000)

        human_click(page, option)
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
        debug_dir = Path(self.config.vfs.debug_dir)
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = str(debug_dir / f"{name}_{int(time.time())}.png")
        try:
            self.page.screenshot(path=path, full_page=True)
            return path
        except Exception:
            logger.exception("Failed to capture debug screenshot")
            return None

    def _step_screenshot(self, name: str) -> None:
        """Best-effort screenshot of a login step, used for visual debugging.
        Controlled by vfs.debug_screenshots; failures are non-fatal."""
        if not self.config.vfs.debug_screenshots:
            return
        path = self.save_debug_screenshot(name)
        if path:
            logger.info("Saved debug screenshot: %s", path)
