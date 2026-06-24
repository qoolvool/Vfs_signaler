import threading
import time

from vfs_bot.main import _interruptible_sleep


class TestInterruptibleSleep:
    def test_sleeps_without_event(self):
        start = time.monotonic()
        result = _interruptible_sleep(0.1, None)
        elapsed = time.monotonic() - start
        assert result is False
        assert elapsed >= 0.08

    def test_returns_false_when_not_interrupted(self):
        event = threading.Event()
        result = _interruptible_sleep(0.1, event)
        assert result is False

    def test_returns_true_when_interrupted(self):
        event = threading.Event()

        def set_later():
            time.sleep(0.05)
            event.set()

        threading.Thread(target=set_later, daemon=True).start()
        start = time.monotonic()
        result = _interruptible_sleep(5.0, event)
        elapsed = time.monotonic() - start
        assert result is True
        assert elapsed < 1.0

    def test_already_set_returns_immediately(self):
        event = threading.Event()
        event.set()
        start = time.monotonic()
        result = _interruptible_sleep(10.0, event)
        elapsed = time.monotonic() - start
        assert result is True
        assert elapsed < 0.5
