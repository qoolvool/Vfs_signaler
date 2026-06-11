import email
import email.message
import email.utils
import imaplib
import re
import time

from .config import IMAPConfig


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
                        received = email.utils.mktime_tz(
                            email.utils.parsedate_tz(msg["Date"])
                        )
                        if received < after_timestamp - 30:
                            continue

                        match = pattern.search(self._get_body(msg))
                        if match:
                            conn.store(msg_id, "+FLAGS", "\\Seen")
                            return match.group(1)
            finally:
                conn.logout()

            time.sleep(self.config.poll_interval_seconds)

        raise TimeoutError("OTP email was not received in time")

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
