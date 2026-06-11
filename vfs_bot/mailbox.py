import email
import email.message
import email.utils
import html
import imaplib
import logging
import re
import time

from .config import IMAPConfig

logger = logging.getLogger(__name__)

# Prefer a number that sits right after an OTP-related keyword; this avoids
# grabbing unrelated digits (tracking ids, years, CSS values) from HTML emails.
OTP_KEYWORD_REGEX = re.compile(
    r"(?:one[\s-]*time[\s-]*password|otp|verification\s+code|"
    r"security\s+code|password|code)\D{0,40}?(\d{4,8})",
    re.IGNORECASE,
)


class OTPMailbox:
    """Reads the one-time-password sent by VFS Global to the applicant's mailbox."""

    def __init__(self, config: IMAPConfig):
        self.config = config

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self.config.host, self.config.port)
        conn.login(self.config.username, self.config.password)
        conn.select(self.config.folder)
        return conn

    def wait_for_otp(self, after_timestamp: float) -> str:
        """Polls the mailbox until an OTP email that arrived after `after_timestamp` is found."""
        deadline = time.time() + self.config.poll_timeout_seconds
        pattern = re.compile(self.config.otp_regex)

        while time.time() < deadline:
            conn = self._connect()
            try:
                status, data = conn.search(
                    None, "UNSEEN", "FROM", f'"{self.config.sender_filter}"'
                )
                if status == "OK":
                    for msg_id in reversed(data[0].split()):
                        status, msg_data = conn.fetch(msg_id, "(RFC822)")
                        if status != "OK" or not msg_data or not msg_data[0]:
                            continue

                        msg = email.message_from_bytes(msg_data[0][1])
                        parsed_date = email.utils.parsedate_tz(msg["Date"] or "")
                        if parsed_date is not None:
                            received = email.utils.mktime_tz(parsed_date)
                            if received < after_timestamp - 30:
                                continue

                        otp = self._extract_otp(self._get_body(msg), pattern)
                        if otp:
                            conn.store(msg_id, "+FLAGS", "\\Seen")
                            return otp
            finally:
                conn.logout()

            time.sleep(self.config.poll_interval_seconds)

        raise TimeoutError("OTP email was not received in time")

    @staticmethod
    def _extract_otp(body: str, fallback_pattern: re.Pattern) -> str | None:
        text = OTPMailbox._html_to_text(body)

        keyword_match = OTP_KEYWORD_REGEX.search(text)
        if keyword_match:
            return keyword_match.group(1)

        fallback_match = fallback_pattern.search(text)
        if fallback_match:
            logger.warning("OTP found via fallback regex (no keyword anchor)")
            return fallback_match.group(1)
        return None

    @staticmethod
    def _html_to_text(content: str) -> str:
        content = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", content)
        content = re.sub(r"(?s)<[^>]+>", " ", content)
        return html.unescape(content)

    @staticmethod
    def _get_body(msg: email.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition") or "")
                if content_type in ("text/plain", "text/html") and "attachment" not in disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")
        return ""
