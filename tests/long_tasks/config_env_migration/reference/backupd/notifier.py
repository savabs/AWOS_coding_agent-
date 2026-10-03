"""E-mail notifications after a backup run."""

import logging
import smtplib
from email.message import EmailMessage
from typing import Callable, Optional

from .config import DEFAULT_SETTINGS_PATH, read_parser, resolve_setting

log = logging.getLogger("backupd.notifier")

SENDER = "backupd@localhost"


class Notifier:
    """Sends a short status mail, or does nothing when no recipient is set."""

    def __init__(
        self,
        recipient: Optional[str],
        smtp_host: str = "localhost",
        smtp_port: int = 25,
        transport: Optional[Callable[[EmailMessage], None]] = None,
    ):
        self.recipient = recipient
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.recipient)

    def build_message(self, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = SENDER
        msg["To"] = self.recipient or ""
        msg["Subject"] = f"[backupd] {subject}"
        msg.set_content(body)
        return msg

    def send(self, subject: str, body: str) -> Optional[EmailMessage]:
        if not self.enabled:
            log.debug("notifications disabled; not sending %r", subject)
            return None
        msg = self.build_message(subject, body)
        if self._transport is not None:
            self._transport(msg)
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as smtp:
                smtp.send_message(msg)
        log.info("sent notification to %s", self.recipient)
        return msg


def build_notifier(settings_path=DEFAULT_SETTINGS_PATH, transport=None) -> Notifier:
    """Create a Notifier from APP_* variables, falling back to [notify]."""
    parser = read_parser(settings_path)  # a missing file just means env/defaults
    recipient = resolve_setting("notify_email", parser=parser)
    host = resolve_setting("smtp_host", parser=parser)
    port = resolve_setting("smtp_port", parser=parser)
    return Notifier(recipient, host, port, transport=transport)
