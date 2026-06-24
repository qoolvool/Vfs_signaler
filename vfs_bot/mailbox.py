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
# grabbing unrelated digits (tracking ids, years, CSS values, phone numbers)
# from HTML emails. The window is generous (80 chars) because real-world VFS
# wording like "The OTP for your application with VFS Global is 453093" puts
# 40+ characters between the keyword and the code.
OTP_KEYWORD_REGEX = re.compile(
    r"(?:one[\s-]*time[\s-]*password|otp|verification\s+code|"
    r"security\s+code|password|code)\D{0,80}?(\d{4,8})",
    re.IGNORECASE,
)


class OTPMailbox:
    """Reads the one-time-password sent by VFS Global to the applicant's mailbox."""

    def __init__(self, config: IMAPConfig):
        self.config = config

    def _connect(self) -> imaplib.IMAP4_SSL:
        logger.info(
            "IMAP: connecting to %s:%s as %s",
            self.config.host,
            self.config.port,
            self.config.username,
        )
        conn = imaplib.IMAP4_SSL(self.config.host, self.config.port)
        conn.login(self.config.username, self.config.password)
        conn.select(self.config.folder)
        logger.info("IMAP: logged in, folder '%s' selected", self.config.folder)
        return conn

    def wait_for_otp(self, after_timestamp: float) -> str:
        """Polls the mailbox until an OTP email that arrived after `after_timestamp` is found."""
        deadline = time.time() + self.config.poll_timeout_seconds
        pattern = re.compile(self.config.otp_regex)
        logger.info(
            "IMAP: waiting up to %ss for OTP email from '%s'",
            self.config.poll_timeout_seconds,
            self.config.sender_filter,
        )

        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                conn = self._connect()
            except Exception:
                logger.exception(
                    "IMAP: failed to connect/login. Check IMAP_USERNAME / "
                    "IMAP_PASSWORD (Gmail needs an app password) and that IMAP "
                    "is enabled in your mailbox settings."
                )
                raise

            try:
                # Search ALL messages from the sender (not only UNSEEN): the
                # user may have opened the email manually, which would clear the
                # UNSEEN flag and hide it. Correctness is ensured by the
                # after_timestamp check below.
                status, data = conn.search(
                    None, "FROM", f'"{self.config.sender_filter}"'
                )
                msg_ids = (
                    data[0].split() if (status == "OK" and data and data[0]) else []
                )
                logger.info(
                    "IMAP: poll #%d — found %d message(s) from '%s'",
                    attempt,
                    len(msg_ids),
                    self.config.sender_filter,
                )
                if not msg_ids:
                    self._log_recent_senders(conn)
                if status == "OK":
                    for msg_id in reversed(msg_ids):
                        status, msg_data = conn.fetch(msg_id, "(RFC822)")
                        if status != "OK" or not msg_data or not msg_data[0]:
                            continue

                        msg = email.message_from_bytes(msg_data[0][1])
                        subject = str(msg["Subject"] or "(no subject)")
                        sender = str(msg["From"] or "(unknown)")
                        date_hdr = str(msg["Date"] or "(no date)")
                        logger.info(
                            "IMAP: examining message — from=%s | subject=%s | date=%s",
                            sender,
                            subject,
                            date_hdr,
                        )

                        parsed_date = email.utils.parsedate_tz(msg["Date"] or "")
                        if parsed_date is not None:
                            received = email.utils.mktime_tz(parsed_date)
                            if received < after_timestamp - 30:
                                logger.info(
                                    "IMAP: skipping — message is older than the "
                                    "login attempt (likely a previous code)"
                                )
                                continue

                        otp = self._extract_otp(self._get_body(msg), pattern)
                        if otp:
                            logger.info("IMAP: OTP extracted from email: %s", otp)
                            conn.store(msg_id, "+FLAGS", "\\Seen")
                            return otp
                        else:
                            logger.warning(
                                "IMAP: no OTP code found in this message body "
                                "(regex did not match)"
                            )
            finally:
                conn.logout()

            logger.info(
                "IMAP: no matching OTP yet, retrying in %ss",
                self.config.poll_interval_seconds,
            )
            time.sleep(self.config.poll_interval_seconds)

        logger.error(
            "IMAP: OTP email not received within %ss. Check that the code was "
            "actually sent, the sender_filter ('%s') matches the real sender, "
            "and the email is in the '%s' folder (not Spam).",
            self.config.poll_timeout_seconds,
            self.config.sender_filter,
            self.config.folder,
        )
        raise TimeoutError("OTP email was not received in time")

    def _log_recent_senders(self, conn: imaplib.IMAP4_SSL, limit: int = 5) -> None:
        """Diagnostic: when no message matches the sender filter, dump the most
        recent messages in the folder so the user can see the real From
        addresses and adjust sender_filter accordingly."""
        try:
            status, data = conn.search(None, "ALL")
            if status != "OK" or not data or not data[0]:
                logger.info("IMAP diagnostic: mailbox appears empty")
                return
            ids = data[0].split()[-limit:]
            logger.info(
                "IMAP diagnostic: last %d message(s) in '%s' (to help set "
                "sender_filter):",
                len(ids),
                self.config.folder,
            )
            for msg_id in reversed(ids):
                status, msg_data = conn.fetch(
                    msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"
                )
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                logger.info(
                    "IMAP diagnostic:   from=%s | subject=%s",
                    str(msg["From"] or "(unknown)"),
                    str(msg["Subject"] or "(no subject)"),
                )
        except Exception:
            logger.exception("IMAP diagnostic: failed to list recent senders")

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
                if (
                    content_type in ("text/plain", "text/html")
                    and "attachment" not in disposition
                ):
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(
                            part.get_content_charset() or "utf-8", errors="ignore"
                        )
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode(
                    msg.get_content_charset() or "utf-8", errors="ignore"
                )
        return ""
