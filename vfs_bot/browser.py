import json
import logging
import os
import tempfile
from pathlib import Path

from patchright.sync_api import BrowserContext, sync_playwright

from .config import ProxyConfig, VFSConfig

logger = logging.getLogger(__name__)

class BrowserSession:
    """Owns the Playwright browser/context lifecycle and persists cookies between runs."""

    def __init__(self, config: VFSConfig, proxy: ProxyConfig | None = None):
        self.config = config
        self.proxy = proxy
        self._playwright = None
        self._browser = None
        self.context: BrowserContext | None = None

    def __enter__(self) -> "BrowserSession":
        self._playwright = sync_playwright().start()

        # patchright recommends launching plain (no extra args, no custom
        # user agent) so its anti-detection patches stay internally
        # consistent with the rest of the browser's fingerprint.
        launch_kwargs: dict = {
            "headless": self.config.headless,
            "channel": "chrome",
        }
        if self.proxy and self.proxy.enabled:
            launch_kwargs["proxy"] = self.proxy.to_playwright()
            logger.info("Routing browser traffic through proxy %s", self.proxy.server)

        try:
            self._browser = self._playwright.chromium.launch(**launch_kwargs)
        except Exception:
            logger.warning(
                "Failed to launch Google Chrome (channel='chrome'), "
                "falling back to bundled Chromium. Install Google Chrome "
                "for better Cloudflare pass rates.",
                exc_info=True,
            )
            launch_kwargs.pop("channel")
            self._browser = self._playwright.chromium.launch(**launch_kwargs)

        storage_state = (
            self.config.storage_state_path
            if Path(self.config.storage_state_path).exists()
            else None
        )
        self.context = self._browser.new_context(
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            storage_state=storage_state,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context:
            self.save_state()
            self.context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def new_page(self):
        return self.context.new_page()

    def save_state(self) -> None:
        """Persists cookies/storage to disk now, so a killed process doesn't
        lose a session that required a fresh login + OTP to obtain."""
        if not self.context:
            return

        target = Path(self.config.storage_state_path)
        state = self.context.storage_state()

        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=str(target.parent) or "."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp_path, target)
        except Exception:
            logger.exception("Failed to save browser storage state")
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def clear_state(self) -> None:
        """Wipes cookies/storage, both in-memory and on disk. Used when VFS
        returns a hard block (Access Denied) so the next attempt starts with
        a completely fresh session instead of a possibly-flagged one."""
        if self.context:
            try:
                self.context.clear_cookies()
            except Exception:
                logger.exception("Failed to clear in-memory cookies")

        target = Path(self.config.storage_state_path)
        try:
            target.unlink()
            logger.info("Deleted %s to force a fresh session", target)
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Failed to delete %s", target)
