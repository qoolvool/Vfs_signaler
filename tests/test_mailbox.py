import re

import pytest

from vfs_bot.mailbox import OTP_KEYWORD_REGEX, OTPMailbox


FALLBACK_PATTERN = re.compile(r"\b(\d{4,8})\b")


class TestExtractOTP:
    """Tests for OTPMailbox._extract_otp (static method)."""

    def test_plain_text_otp(self):
        body = "Your one-time password is 453093. Do not share it."
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "453093"

    def test_otp_keyword(self):
        body = "The OTP for your application with VFS Global is 123456"
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "123456"

    def test_verification_code_keyword(self):
        body = "Your verification code: 8877"
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "8877"

    def test_security_code_keyword(self):
        body = "Please use this security code 554433 to proceed."
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "554433"

    def test_html_email(self):
        body = (
            "<html><body><p>Dear user,</p>"
            "<p>Your OTP is <b>987654</b>.</p>"
            "</body></html>"
        )
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "987654"

    def test_html_with_script_tags_ignored(self):
        body = (
            "<html><head><script>var x = 111222;</script></head>"
            "<body>Your OTP is 334455</body></html>"
        )
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "334455"

    def test_html_with_style_tags_ignored(self):
        body = (
            "<html><head><style>.otp { font-size: 24px; }</style></head>"
            "<body>Your code is 667788</body></html>"
        )
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "667788"

    def test_no_otp_returns_none(self):
        body = "Thank you for your application. No codes here."
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) is None

    def test_fallback_regex_when_no_keyword(self):
        body = "Here is your number: 5566"
        result = OTPMailbox._extract_otp(body, FALLBACK_PATTERN)
        assert result == "5566"

    def test_long_distance_keyword_to_code(self):
        body = (
            "The OTP for your application with VFS Global "
            "submitted for Croatia is 453093"
        )
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "453093"

    def test_four_digit_code(self):
        body = "Your one-time password is 1234."
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "1234"

    def test_eight_digit_code(self):
        body = "OTP: 12345678"
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "12345678"

    def test_html_entities_decoded(self):
        body = "<p>Your OTP is 112233&period;</p>"
        assert OTPMailbox._extract_otp(body, FALLBACK_PATTERN) == "112233"


class TestHtmlToText:
    def test_strips_tags(self):
        assert "hello" in OTPMailbox._html_to_text("<b>hello</b>")

    def test_strips_script(self):
        result = OTPMailbox._html_to_text("<script>alert(1)</script>real text")
        assert "alert" not in result
        assert "real text" in result

    def test_strips_style(self):
        result = OTPMailbox._html_to_text("<style>.a{color:red}</style>visible")
        assert "color" not in result
        assert "visible" in result

    def test_decodes_entities(self):
        result = OTPMailbox._html_to_text("5 &gt; 3 &amp; 2 &lt; 4")
        assert "5 > 3 & 2 < 4" in result


class TestOTPKeywordRegex:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("one-time password is 123456", "123456"),
            ("OTP: 654321", "654321"),
            ("verification code 998877", "998877"),
            ("security code: 445566", "445566"),
            ("Your password is 7788", "7788"),
            ("code 9900", "9900"),
        ],
    )
    def test_keyword_matches(self, text, expected):
        m = OTP_KEYWORD_REGEX.search(text)
        assert m is not None
        assert m.group(1) == expected

    def test_case_insensitive(self):
        m = OTP_KEYWORD_REGEX.search("ONE-TIME PASSWORD IS 123456")
        assert m is not None
        assert m.group(1) == "123456"
