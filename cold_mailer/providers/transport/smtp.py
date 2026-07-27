"""Generic SMTP transport — the portable fallback for any provider not
covered by a dedicated client (Gmail API is the recommended path for the
personal-Gmail sending this project actually uses; see `gmail.py`). Uses
the standard library's `smtplib`, pushed to a thread since it's a blocking
API.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from cold_mailer.core.config import get_settings
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.transport.base import SendResult, Transport


class SMTPTransport(Transport):
    name = "smtp"

    def __init__(
        self, host: str, port: int = 587, username: str | None = None, password: str | None = None
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    @network_retry(max_attempts=3)
    async def send(
        self, to_email: str, subject: str, body: str, thread_id: str | None = None
    ) -> SendResult:
        return await asyncio.to_thread(self._send_sync, to_email, subject, body)

    def _send_sync(self, to_email: str, subject: str, body: str) -> SendResult:
        settings = get_settings().delivery
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{settings.from_name} <{settings.from_email}>" if settings.from_name else settings.from_email
        msg["To"] = to_email
        msg.set_content(body)

        with smtplib.SMTP(self.host, self.port, timeout=15) as server:
            server.starttls()
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(msg)

        message_id = msg["Message-ID"] or f"smtp-{hash((to_email, subject))}"
        return SendResult(provider_message_id=str(message_id))
