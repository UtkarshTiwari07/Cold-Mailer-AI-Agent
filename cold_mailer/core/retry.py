"""Shared retry policies.

Two policies cover the whole system: `network_retry` for anything doing I/O
against a flaky external service (search, crawl, ATS APIs, SMTP/Gmail), and
`llm_retry` for LLM calls, which layers on top of PydanticAI's own
output-validation retries (that layer re-asks the model to fix a schema
violation; this layer covers transient transport failures around the whole
call).
"""

from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

TRANSIENT_HTTP_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


def network_retry(max_attempts: int = 3):
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=1, max=20),
        retry=retry_if_exception_type(TRANSIENT_HTTP_EXCEPTIONS),
    )


def llm_retry(max_attempts: int = 3):
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=2, max=30),
        retry=retry_if_exception_type(TRANSIENT_HTTP_EXCEPTIONS + (TimeoutError,)),
    )
