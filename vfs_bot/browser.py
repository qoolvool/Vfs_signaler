from pathlib import Path

from playwright.sync_api import BrowserContext, sync_playwright
from playwright_stealth import Stealth

from .config import VFSConfig

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BrowserSession:
    """Owns the Playwright browser/context lifecycle and persists cookies between runs."""

    def __init__(self, config: VFSConfig):
        self.config = config
        self._playwright = None
        self._browser = None
        self.context: BrowserContext | None = None

    def __enter__(self) -> "BrowserSession":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.config.headless)

        storage_state = (
            self.config.storage_state_path
            if Path(self.config.storage_state_path).exists()
            else None
        )
        self.context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            storage_state=storage_state,
        )
        Stealth().apply_stealth_sync(self.context)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context:
            self.context.storage_state(path=self.config.storage_state_path)
            self.context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def new_page(self):
        return self.context.new_page()
