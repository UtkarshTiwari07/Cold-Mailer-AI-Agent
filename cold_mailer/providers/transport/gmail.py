"""Gmail API transport — the transport this project actually uses, sending
from the operator's personal Gmail account per the explicit, risk-accepted
decision to do so (see DESIGN.md's deliverability section for the warm-up
ramp and circuit breaker that exist specifically to manage that risk).

OAuth (not SMTP/app-password) because it's what Google's own bulk-sender
guidance treats as the trusted path, and because it gives real Gmail
threading via `threadId` for follow-ups — a reply lands in the same Gmail
conversation the recipient already has open, not a new thread.

Lazily imported: `google-api-python-client` / `google-auth-oauthlib` are in
the `full` extra, so a bare install of this project never needs them unless
Gmail sending is actually configured.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from pathlib import Path

from cold_mailer.core.config import get_settings
from cold_mailer.core.retry import network_retry
from cold_mailer.providers.transport.base import SendResult, Transport

_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class GmailTransport(Transport):
    name = "gmail"

    def __init__(self) -> None:
        try:
            import google_auth_oauthlib.flow  # noqa: F401
            import googleapiclient.discovery  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "GmailTransport requires the 'full' extra: "
                "pip install -e '.[full]'"
            ) from exc
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        settings = get_settings().delivery
        token_path = Path(settings.gmail_token_path)
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    settings.gmail_client_secrets, _SCOPES
                )
                # First-run only: opens a local browser for consent. Every
                # subsequent run reuses the refreshed token on disk — this
                # deployment never needs interactive auth again after setup.
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())

        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    @network_retry(max_attempts=3)
    async def send(
        self, to_email: str, subject: str, body: str, thread_id: str | None = None
    ) -> SendResult:
        import asyncio

        return await asyncio.to_thread(self._send_sync, to_email, subject, body, thread_id)

    def _send_sync(self, to_email: str, subject: str, body: str, thread_id: str | None) -> SendResult:
        settings = get_settings().delivery
        service = self._get_service()

        msg = EmailMessage()
        msg["To"] = to_email
        msg["From"] = f"{settings.from_name} <{settings.from_email}>" if settings.from_name else settings.from_email
        msg["Subject"] = subject
        msg.set_content(body)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        body_payload: dict = {"raw": raw}
        if thread_id:
            body_payload["threadId"] = thread_id

        sent = service.users().messages().send(userId="me", body=body_payload).execute()
        return SendResult(provider_message_id=sent["id"], provider_thread_id=sent.get("threadId"))
