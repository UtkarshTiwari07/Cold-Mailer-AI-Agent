"""Default transport: prints the email to stdout/log instead of sending it.
This is what `CM_DELIVERY__TRANSPORT=console` (the shipped default) uses —
every other transport is opt-in, which is the safety property that lets
`make demo` and the whole test suite run the pipeline to completion without
any risk of a real send."""

from __future__ import annotations

import hashlib
import time

from cold_mailer.core.logging import get_logger
from cold_mailer.providers.transport.base import SendResult, Transport

log = get_logger(component="transport.console")


class ConsoleTransport(Transport):
    name = "console"

    async def send(
        self, to_email: str, subject: str, body: str, thread_id: str | None = None
    ) -> SendResult:
        fake_id = hashlib.sha256(f"{to_email}{subject}{time.time()}".encode()).hexdigest()[:16]
        print("\n" + "=" * 70)
        print(f"[CONSOLE TRANSPORT — not actually sent] To: {to_email}")
        print(f"Subject: {subject}")
        if thread_id:
            print(f"(threaded reply to {thread_id})")
        print("-" * 70)
        print(body)
        print("=" * 70 + "\n")
        log.info("transport.console.sent", to=to_email, subject=subject, fake_id=fake_id)
        return SendResult(provider_message_id=f"console-{fake_id}", provider_thread_id=thread_id or fake_id)
