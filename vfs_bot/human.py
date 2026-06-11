import random
import time

from playwright.sync_api import Locator, Page


def random_delay(min_ms: int = 300, max_ms: int = 900) -> None:
    """Sleeps for a random duration to break up perfectly regular timing."""
    time.sleep(random.uniform(min_ms, max_ms) / 1000)


def human_type(locator: Locator, text: str, min_delay_ms: int = 40, max_delay_ms: int = 160) -> None:
    """Types text one character at a time with randomized inter-key delays,
    instead of Playwright's instant fill()."""
    locator.scroll_into_view_if_needed()
    locator.click()
    random_delay(150, 400)

    # Clear any pre-filled value the way a user would (select-all + delete).
    locator.press("Control+A")
    random_delay(30, 100)
    locator.press("Backspace")
    random_delay(50, 150)

    for char in text:
        locator.press_sequentially(char, delay=0)
        time.sleep(random.uniform(min_delay_ms, max_delay_ms) / 1000)


def human_click(page: Page, locator: Locator) -> None:
    """Moves the mouse towards the element before clicking it."""
    locator.scroll_into_view_if_needed()
    random_delay(100, 300)

    box = locator.bounding_box()
    if box:
        x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        page.mouse.move(x, y, steps=random.randint(10, 25))
        random_delay(100, 250)

    locator.click()
    random_delay(200, 600)
