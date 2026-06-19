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

# VFS locks the account/session for a while after too many requests. Unlike
# ACCESS_DENIED_PATTERNS (Cloudflare-level block), this is account-level and
# the page itself says access resets after a couple of hours.
ACCOUNT_LOCKED_PATTERNS = (
    re.compile(r"account locked", re.I),
    re.compile(r"429202"),
)


class AccessDeniedError(RuntimeError):
    """Raised when VFS/Cloudflare returns a hard block page."""


class RequestTimedOutError(RuntimeError):
    """Raised when VFS returns a 'Request Timed Out (504)' error page."""


class AccountLockedError(RuntimeError):
    """Raised when VFS returns an 'Account Locked (429202)' page."""


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
        self.check_account_locked()
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

    def check_account_locked(self) -> None:
        """Raises AccountLockedError if VFS returned an 'Account Locked
        (429202)' page."""
        try:
            content = self.page.content()
        except Exception:
            return
        for pattern in ACCOUNT_LOCKED_PATTERNS:
            if pattern.search(content):
                raise AccountLockedError(
                    f"VFS returned an Account Locked (429202) page (matched: {pattern.pattern!r})"
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
        self.check_account_locked()
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
        self.check_account_locked()

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
        self.check_account_locked()

        logger.info("Login step: clicking 'Start New Booking'")
        self._click_first(
            [
                lambda: page.locator("button:has-text('Start New Booking'):visible"),
                lambda: page.get_by_role(
                    "button", name=re.compile("start new booking", re.I)
                ),
            ]
        )

        # VFS is an Angular SPA — clicking the button triggers a client-side
        # route change, not a full page load. Wait for the appointment form
        # to appear instead of waiting for a URL navigation event.
        logger.info("Login step: waiting for appointment form to appear")
        page.locator("mat-select[formcontrolname='centerCode']").first.wait_for(
            state="visible", timeout=60000
        )

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

        logger.info("Appointment form: selecting Application Centre = '%s'", vfs.application_centre)
        self._select_dropdown("Choose your Application Centre", vfs.application_centre, "centerCode")
        self._step_screenshot("10_after_centre")

        logger.info("Appointment form: selecting category = '%s'", vfs.category)
        self._select_dropdown("Choose your appointment category", vfs.category, "selectedSubvisaCategory")
        self._step_screenshot("11_after_category")

        logger.info("Appointment form: selecting sub-category = '%s'", vfs.sub_category)
        self._select_dropdown("Choose your sub-category", vfs.sub_category, "visaCategoryCode")
        self._step_screenshot("12_after_subcategory")

        page.wait_for_timeout(3000)
        self._step_screenshot("13_final_form_state")

        no_slots = page.get_by_text(NO_SLOTS_TEXT, exact=False)
        count = no_slots.count()
        logger.info("'%s' matched %d time(s) on the page", NO_SLOTS_TEXT, count)
        return count == 0

    def _select_dropdown(self, label_text: str, value: str, formcontrolname: str) -> None:
        """Selects `value` in the mat-select identified by `formcontrolname`.

        VFS's mat-select labels often have `for`/`aria-labelledby` attributes
        that don't actually point at the right control, so we go straight to
        the Angular form control instead of relying on label text.
        """
        page = self.page

        trigger = page.locator(f"mat-select[formcontrolname='{formcontrolname}']").first
        for attempt in range(2):
            try:
                trigger.wait_for(state="visible", timeout=8000)
                self._select_custom_dropdown(trigger, value)
                return
            except Exception:
                logger.exception(
                    "Attempt %d: failed to select '%s' (formcontrolname='%s')",
                    attempt + 1,
                    label_text,
                    formcontrolname,
                )
                page.wait_for_timeout(1500)

        logger.warning("Giving up on dropdown '%s'", label_text)

    def _select_custom_dropdown(self, trigger: Locator, value: str) -> None:
        """Clicks the mat-select to open its options panel, then picks the
        option whose text best matches `value` from the options actually
        offered, instead of assuming `value` is present verbatim."""
        page = self.page
        target = self._normalize_text(value)

        try:
            current = self._normalize_text(trigger.inner_text())
            if current == target:
                logger.info("'%s' is already selected, skipping", value)
                return
        except Exception:
            pass

        for attempt in range(3):
            try:
                logger.info("Attempt %d: opening dropdown to pick '%s'", attempt + 1, value)
                human_click(page, trigger)
                page.wait_for_timeout(500)
                self._step_screenshot(f"dropdown_open_{attempt + 1}")

                options = page.get_by_role("option")
                try:
                    options.first.wait_for(state="visible", timeout=5000)
                except Exception:
                    logger.warning("No options appeared after clicking trigger for '%s'", value)
                    self._step_screenshot(f"dropdown_no_options_{attempt + 1}")
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(1000)
                    continue

                texts = options.all_inner_texts()
                logger.info("Available options for '%s': %r", value, texts)

                match_index = None
                for i, text in enumerate(texts):
                    norm = self._normalize_text(text)
                    if norm == target or target in norm or norm in target:
                        match_index = i
                        break

                if match_index is None:
                    logger.warning("No option matching '%s' among %r", value, texts)
                    self._step_screenshot(f"dropdown_no_match_{attempt + 1}")
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(1000)
                    continue

                logger.info("Clicking option [%d] %r for '%s'", match_index, texts[match_index], value)
                human_click(page, options.nth(match_index))

                # After selecting a value the page reloads/re-renders the
                # form (Angular fetches the next set of options, etc.).
                # Wait for it to settle before verifying or moving on.
                logger.info("Waiting for page to settle after selecting '%s'", value)
                page.wait_for_timeout(5000)
                self._step_screenshot(f"dropdown_after_select_{attempt + 1}")

                try:
                    current = self._normalize_text(trigger.inner_text())
                except Exception:
                    logger.info("Trigger detached after selecting '%s', re-locating", value)
                    page.wait_for_timeout(2000)
                    current = self._normalize_text(trigger.inner_text())

                if current == target or target in current or current in target:
                    logger.info("'%s' successfully selected (trigger shows '%s')", value, current)
                    return
                logger.warning(
                    "After selecting, trigger shows '%s' (expected '%s'), retrying",
                    current,
                    value,
                )
            except Exception:
                logger.exception("Attempt %d to select '%s' failed, retrying", attempt + 1, value)

            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

        logger.warning("Giving up selecting '%s' after retries", value)

    # ------------------------------------------------------------------
    # Locator helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

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
