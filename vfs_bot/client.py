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

# Phrases shown by Cloudflare/VFS when a request is blocked outright (as
# opposed to a normal challenge). If the session/cookies are stale or flagged,
# clearing storage_state.json and starting a fresh session is the usual fix.
ACCESS_DENIED_PATTERNS = (
    re.compile(r"access denied", re.I),
    re.compile(r"unauthoris(?:ed|zed)\s+activity", re.I),
    re.compile(r"429002"),
    re.compile(r"you have been blocked", re.I),
    re.compile(r"attention required", re.I),
    re.compile(r"error\s*1020", re.I),
)

# VFS occasionally returns a "Request Timed Out (504)" error page (its own
# gateway timing out, not an anti-bot block). This is transient — the usual
# fix is to back off for a while and retry from scratch.
REQUEST_TIMEOUT_PATTERNS = (
    re.compile(r"request timed out", re.I),
    re.compile(r"\(504\)"),
)


class AccessDeniedError(RuntimeError):
    """Raised when VFS/Cloudflare returns a hard block page."""


class RequestTimedOutError(RuntimeError):
    """Raised when VFS returns a 'Request Timed Out (504)' error page."""


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
        self.check_access_denied()
        self.check_request_timeout()
        self._accept_cookies()
        try:
            self.page.wait_for_url(re.compile(r"/login", re.I), timeout=5000)
            return False
        except PlaywrightTimeoutError:
            return bool(re.search(r"/login", self.page.url, re.I)) is False

    def check_access_denied(self) -> None:
        """Raises AccessDeniedError if the current page is a Cloudflare/VFS
        hard block page (as opposed to a normal challenge)."""
        try:
            content = self.page.content()
        except Exception:
            return
        for pattern in ACCESS_DENIED_PATTERNS:
            if pattern.search(content):
                raise AccessDeniedError(
                    f"VFS/Cloudflare returned a block page (matched: {pattern.pattern!r})"
                )

    def check_request_timeout(self) -> None:
        """Raises RequestTimedOutError if VFS returned a 'Request Timed Out
        (504)' error page."""
        try:
            content = self.page.content()
        except Exception:
            return
        for pattern in REQUEST_TIMEOUT_PATTERNS:
            if pattern.search(content):
                raise RequestTimedOutError(
                    f"VFS returned a Request Timed Out (504) page (matched: {pattern.pattern!r})"
                )

    # ------------------------------------------------------------------
    # Login + OTP flow
    # ------------------------------------------------------------------
    def login(self) -> None:
        page = self.page
        vfs = self.config.vfs

        logger.info("Login step: opening login page %s", vfs.login_url)
        page.goto(vfs.login_url, wait_until="domcontentloaded")
        random_delay(500, 1500)
        self.check_access_denied()
        self.check_request_timeout()
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
        self.check_access_denied()
        self.check_request_timeout()

        logger.info("Login step: waiting for OTP input field")
        otp_input = self._first_visible(
            [
                lambda: page.locator("input[formcontrolname='otp']"),
                lambda: page.locator("input[name='otp']"),
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
        self.check_access_denied()
        self.check_request_timeout()

        logger.info("Login step: waiting for redirect to appointment page")
        try:
            page.wait_for_url(re.compile(r"book-appointment", re.I), timeout=15000)
        except PlaywrightTimeoutError:
            logger.info(
                "Login step: not on appointment page yet, clicking 'Start New Booking'"
            )
            self._click_first(
                [
                    lambda: page.locator("button:has-text('Start New Booking'):visible"),
                    lambda: page.get_by_role(
                        "button", name=re.compile("start new booking", re.I)
                    ),
                ]
            )
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
        next_click_at = time.time() + 2  # give it a brief moment to auto-pass first
        while time.time() < deadline:
            if self._cloudflare_passed():
                logger.info("Cloudflare challenge passed")
                return
            # If it hasn't auto-passed, keep retrying the checkbox click every
            # few seconds (some configs require an explicit interaction, and the
            # widget iframe may only appear after a short delay).
            if time.time() >= next_click_at:
                self._click_turnstile_checkbox()
                next_click_at = time.time() + 3
            page.wait_for_timeout(500)

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

    # Selectors that may host or be the Turnstile widget. The widget VFS embeds
    # is usually a div.cf-turnstile containing a cross-origin iframe; depending
    # on the build, either the iframe or the container is the clickable target.
    TURNSTILE_SELECTORS = (
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[title*='Cloudflare' i]",
        "iframe[title*='challenge' i]",
        "iframe[title*='widget' i]",
        ".cf-turnstile",
        "div[class*='turnstile' i]",
        "#cf-turnstile",
    )

    def _click_turnstile_checkbox(self) -> bool:
        """Best-effort: locate the Cloudflare Turnstile widget, move the mouse
        onto its checkbox via a curved path, and click. Returns True if a click
        was issued. Non-fatal on failure."""
        page = self.page

        # Prefer clicking the actual checkbox label inside the challenge
        # iframe — this hits the real element directly instead of guessing
        # coordinates from the outer iframe's bounding box, which is faster
        # and more reliable.
        if self._click_turnstile_in_frame():
            return True

        box = None
        matched = None
        for selector in self.TURNSTILE_SELECTORS:
            try:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                # Don't require "visible" — Turnstile iframes often report as
                # not visible to Playwright even when on screen. A bounding box
                # is enough to aim a real mouse click.
                candidate = locator.bounding_box()
                if candidate and candidate["width"] > 0 and candidate["height"] > 0:
                    box = candidate
                    matched = selector
                    break
            except Exception:
                continue

        if not box:
            logger.info("No Cloudflare Turnstile widget found to click yet")
            return False

        # The checkbox sits near the left edge of the widget, vertically
        # centred. Aim for that area with a little jitter.
        x = box["x"] + min(box["width"] * 0.5, 30) + random.uniform(-4, 4)
        y = box["y"] + box["height"] * 0.5 + random.uniform(-4, 4)
        try:
            human_mouse_move_to(page, x, y)
            random_delay(150, 400)
            page.mouse.click(x, y)
            logger.info("Clicked Cloudflare Turnstile checkbox area (%s)", matched)
            random_delay(400, 900)
            return True
        except Exception:
            logger.exception("Failed to click Cloudflare Turnstile checkbox")
            return False

    def _click_turnstile_in_frame(self) -> bool:
        """Click the checkbox element inside the Turnstile iframe via a frame
        locator (Playwright can reach cross-origin frames)."""
        page = self.page
        try:
            frame = page.frame_locator(
                "iframe[src*='challenges.cloudflare.com'], "
                "iframe[title*='Cloudflare' i], iframe[title*='challenge' i]"
            )
            checkbox = frame.locator(
                "label.cb-lb, .cb-i, input[type='checkbox'], .cb-c, #challenge-stage"
            ).first
            checkbox.click(timeout=3000)
            logger.info("Clicked Turnstile checkbox inside iframe")
            random_delay(400, 900)
            return True
        except Exception:
            logger.info("Could not click checkbox inside Turnstile iframe")
            return False

    # ------------------------------------------------------------------
    # Appointment availability
    # ------------------------------------------------------------------
    def has_available_slot(self) -> bool:
        """Checks the already-open appointment page for available slots.
        Assumes `open_appointments()` was called first."""
        page = self.page
        vfs = self.config.vfs

        self._select_dropdown("Choose your Application Centre", vfs.application_centre, "centerCode")
        random_delay(300, 800)
        self._select_dropdown("Choose your appointment category", vfs.category, "selectedSubvisaCategory")
        random_delay(300, 800)
        self._select_dropdown("Choose your sub-category", vfs.sub_category, "visaCategoryCode")

        page.wait_for_timeout(2000)

        no_slots = page.get_by_text(NO_SLOTS_TEXT, exact=False)
        return no_slots.count() == 0

    def _select_dropdown(self, label_text: str, value: str, formcontrolname: str | None = None) -> None:
        page = self.page

        # 0) Direct match via Angular's formcontrolname attribute. This is the
        # most reliable option: VFS's mat-select labels often have `for`/
        # `aria-labelledby` attributes that don't actually point at the right
        # control, so text-based lookups below can grab the wrong dropdown.
        if formcontrolname:
            trigger = page.locator(f"mat-select[formcontrolname='{formcontrolname}']").first
            try:
                trigger.wait_for(state="visible", timeout=5000)
                self._select_custom_dropdown(trigger, value)
                return
            except Exception:
                logger.exception(
                    "Failed to select '%s' via formcontrolname='%s', falling back",
                    label_text,
                    formcontrolname,
                )

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
        trigger = container.locator(
            "[role='combobox'], [role='listbox'], "
            "input[readonly], .dropdown, .select, button"
        ).first
        trigger.wait_for(state="visible", timeout=5000)
        self._select_custom_dropdown(trigger, value)

    def _select_custom_dropdown(self, trigger: Locator, value: str) -> None:
        page = self.page

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
