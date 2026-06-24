from unittest.mock import MagicMock

import pytest

from vfs_bot.client import (
    ACCESS_DENIED_PATTERNS,
    ACCESS_RESTRICTED_PATTERNS,
    ACCOUNT_LOCKED_PATTERNS,
    REQUEST_TIMEOUT_PATTERNS,
    SESSION_EXPIRED_PATTERNS,
    AccessDeniedError,
    AccessRestrictedError,
    AccountLockedError,
    RequestTimedOutError,
    SessionExpiredError,
    VFSClient,
)
from vfs_bot.config import AppConfig, IMAPConfig, ProxyConfig, TelegramConfig, VFSConfig


def _make_config(**overrides) -> AppConfig:
    vfs_kw = dict(
        login_url="https://example.com/login",
        appointment_url="https://example.com/book",
        application_centre="Centre,City",
        category="C visa",
        sub_category="Tourist",
    )
    vfs_kw.update(overrides)
    return AppConfig(
        vfs=VFSConfig(**vfs_kw),
        imap=IMAPConfig(host="imap.example.com"),
        telegram=TelegramConfig(),
        proxy=ProxyConfig(),
    )


def _make_client(page_content="<html></html>", **config_overrides) -> VFSClient:
    page = MagicMock()
    page.content.return_value = page_content
    config = _make_config(**config_overrides)
    mailbox = MagicMock()
    notifier = MagicMock()
    return VFSClient(page, config, mailbox, notifier)


# ---- Pattern matching tests ----


class TestErrorPatterns:
    @pytest.mark.parametrize(
        "text",
        [
            "Access Denied",
            "access denied",
            "Unauthorised Activity detected",
            "Error code: 429002",
            "You have been blocked",
            "Attention Required!",
            "Error 1020",
        ],
    )
    def test_access_denied_patterns(self, text):
        assert any(p.search(text) for p in ACCESS_DENIED_PATTERNS)

    @pytest.mark.parametrize(
        "text",
        [
            "Request Timed Out",
            "request timed out",
            "Error (504)",
        ],
    )
    def test_request_timeout_patterns(self, text):
        assert any(p.search(text) for p in REQUEST_TIMEOUT_PATTERNS)

    @pytest.mark.parametrize(
        "text",
        [
            "Account Locked",
            "account locked",
            "429202",
        ],
    )
    def test_account_locked_patterns(self, text):
        assert any(p.search(text) for p in ACCOUNT_LOCKED_PATTERNS)

    @pytest.mark.parametrize(
        "text",
        [
            "Session Expired",
            "session expired",
            "Session is Invalid",
            "session invalid",
        ],
    )
    def test_session_expired_patterns(self, text):
        assert any(p.search(text) for p in SESSION_EXPIRED_PATTERNS)

    @pytest.mark.parametrize(
        "text",
        [
            "Access Restricted",
            "access restricted for User ID",
            "429001",
        ],
    )
    def test_access_restricted_patterns(self, text):
        assert any(p.search(text) for p in ACCESS_RESTRICTED_PATTERNS)

    @pytest.mark.parametrize(
        "text",
        [
            "Welcome to VFS Global",
            "Book your appointment",
            "no appointment slots",
        ],
    )
    def test_no_false_positives(self, text):
        all_patterns = (
            ACCESS_DENIED_PATTERNS
            + REQUEST_TIMEOUT_PATTERNS
            + ACCOUNT_LOCKED_PATTERNS
            + SESSION_EXPIRED_PATTERNS
            + ACCESS_RESTRICTED_PATTERNS
        )
        assert not any(p.search(text) for p in all_patterns)


# ---- Error check methods ----


class TestCheckMethods:
    def test_check_access_denied_raises(self):
        client = _make_client(page_content="<html>Access Denied</html>")
        with pytest.raises(AccessDeniedError):
            client.check_access_denied()

    def test_check_access_denied_clean_page(self):
        client = _make_client(page_content="<html>Welcome</html>")
        client.check_access_denied()

    def test_check_request_timeout_raises(self):
        client = _make_client(page_content="<html>Request Timed Out (504)</html>")
        with pytest.raises(RequestTimedOutError):
            client.check_request_timeout()

    def test_check_account_locked_raises(self):
        client = _make_client(page_content="<html>Account Locked 429202</html>")
        with pytest.raises(AccountLockedError):
            client.check_account_locked()

    def test_check_session_expired_raises(self):
        client = _make_client(page_content="<html>Session Expired</html>")
        with pytest.raises(SessionExpiredError):
            client.check_session_expired()

    def test_check_access_restricted_raises(self):
        client = _make_client(page_content="<html>Access Restricted 429001</html>")
        with pytest.raises(AccessRestrictedError):
            client.check_access_restricted()

    def test_check_tolerates_page_content_error(self):
        client = _make_client()
        client.page.content.side_effect = Exception("browser crashed")
        client.check_access_denied()
        client.check_request_timeout()
        client.check_account_locked()
        client.check_session_expired()
        client.check_access_restricted()


# ---- Utility methods ----


class TestNormalizeText:
    def test_lowercases(self):
        assert VFSClient._normalize_text("Hello World") == "hello world"

    def test_collapses_whitespace(self):
        assert VFSClient._normalize_text("  a   b  ") == "a b"

    def test_strips(self):
        assert VFSClient._normalize_text("  hello  ") == "hello"

    def test_tabs_and_newlines(self):
        assert VFSClient._normalize_text("a\n\tb") == "a b"


# ---- Screenshot ----


class TestSaveDebugScreenshot:
    def test_returns_path_on_success(self, tmp_path):
        client = _make_client(debug_dir=str(tmp_path / "debug"))
        client.page.screenshot.return_value = None
        path = client.save_debug_screenshot("test")
        assert path is not None
        assert "test" in path

    def test_returns_none_on_failure(self, tmp_path):
        client = _make_client(debug_dir=str(tmp_path / "debug"))
        client.page.screenshot.side_effect = Exception("fail")
        assert client.save_debug_screenshot("test") is None
