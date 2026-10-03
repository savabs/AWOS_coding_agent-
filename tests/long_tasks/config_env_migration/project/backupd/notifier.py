"""E-mail notifications after a backup run."""

import configparser
import logging
import smtplib
from email.message import EmailMessage
from typing import Callable, Optional

from .config import DEFAULT_SETTINGS_PATH, ConfigError

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
    """Create a Notifier from the [notify] section of the settings file."""
    parser = configparser.ConfigParser()
    parser.read(settings_path)  # a missing file just means "no notifications"
    recipient = parser.get("notify", "notify_email", fallback="").strip() or None
    host = parser.get("notify", "smtp_host", fallback="localhost").strip()
    try:
        port = parser.getint("notify", "smtp_port", fallback=25)
    except ValueError as exc:
        raise ConfigError("setting 'smtp_port' must be an integer") from exc
    return Notifier(recipient, host, port, transport=transport)
