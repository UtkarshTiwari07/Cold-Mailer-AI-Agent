"""Email transport abstraction. `ConsoleTransport` is the default and the
only one that requires zero configuration — every other transport needs
real credentials, so shipping console-first means the full pipeline can be
demoed, tested, and reviewed end-to-end without ever risking a real send.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class SendResult(BaseModel):
    provider_message_id: str
    provider_thread_id: str | None = None


class Transport(ABC):
    name: str

    @abstractmethod
    async def send(
        self, to_email: str, subject: str, body: str, thread_id: str | None = None
    ) -> SendResult:
        """Send one plain-text email. `thread_id`, when given, threads the
        message as a reply to a prior touch (used for follow-ups 2/3) —
        implementations that can't thread should just ignore it rather than
        fail, since a non-threaded follow-up is still a legitimate send."""
