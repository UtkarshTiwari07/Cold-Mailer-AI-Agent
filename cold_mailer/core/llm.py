"""LLM client: PydanticAI + DeepSeek V4, with a persistent response cache and
a hard spend ceiling.

Two things about DeepSeek V4 drive this file's shape:

1. `deepseek-chat` / `deepseek-reasoner` were retired 2026-07-24 15:59 UTC —
   three days before this was written — and now return HTTP 400. The live
   IDs are `deepseek-v4-flash` / `deepseek-v4-pro`, set in
   `core/config.py::LLMSettings`. PydanticAI's bundled `DeepSeekProvider`
   already knows both names and defaults to `https://api.deepseek.com`.

2. Flash is $0.14/1M input tokens on a cache miss and $0.0028/1M on a cache
   hit — a 50x difference. DeepSeek's server-side prefix cache keys on a
   byte-identical prompt prefix, which in chat-completion terms means the
   system/instructions message. So every agent's prompt builder MUST put
   stable content (role instructions, the candidate profile, the style
   guide, few-shot examples) in the system prompt and ONLY the per-company
   variable data in the user prompt. Get this backwards and nothing breaks —
   it just quietly costs 50x more on every call.

DeepSeek exposes `json_object` mode, not a strict `json_schema` mode, so the
model is not schema-constrained at the API level — it can and does drift.
PydanticAI's own validate-and-retry loop (the `retries=` on `Agent`) is what
makes this reliable, which is the deciding factor in choosing PydanticAI
over a framework that assumes schema-constrained output.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from cold_mailer.core.cache import RedisCache, get_redis
from cold_mailer.core.config import get_settings
from cold_mailer.core.db import acquire
from cold_mailer.core.logging import get_logger
from cold_mailer.core.retry import llm_retry

T = TypeVar("T", bound=BaseModel)

log = get_logger(component="llm")

Tier = Literal["flash", "pro"]

# Per 1M tokens. Checked against api-docs.deepseek.com 2026-07-27.
_PRICING = {
    "deepseek-v4-flash": {"in": 0.14, "cache_in": 0.0028, "out": 0.28},
    "deepseek-v4-pro": {"in": 0.435, "cache_in": 0.435, "out": 0.87},
}


class BudgetExceededError(RuntimeError):
    """Raised instead of making a call once the configured spend ceiling is
    reached. The run should stop, not silently keep billing."""


@dataclass
class LLMResult:
    value: BaseModel
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    cache_hit: bool
    latency_ms: int


def _bare_model_id(model_ref: str) -> str:
    """'deepseek/deepseek-v4-flash' -> 'deepseek-v4-flash'."""
    return model_ref.split("/", 1)[-1]


def model_for_tier(tier: Tier) -> str:
    s = get_settings().llm
    return s.flash_model if tier == "flash" else s.pro_model


def _hash_prompt(system_prompt: str, user_prompt: str, model: str, schema_version: str) -> str:
    h = hashlib.sha256()
    for part in (system_prompt, user_prompt, model, schema_version):
        h.update(part.encode())
        h.update(b"\x00")
    return h.hexdigest()


_agents: dict[str, Agent] = {}


def _get_agent(model_ref: str, output_type: type[BaseModel]) -> Agent:
    """One Agent per (model, output_type). The Agent object carries no
    system prompt — that's supplied per-call via `instructions=`, so the
    same Agent instance serves every caller wanting this (model, schema)
    combination regardless of which agent's prompt they're running."""
    key = f"{model_ref}:{output_type.__qualname__}"
    if key not in _agents:
        settings = get_settings().llm
        model = OpenAIChatModel(
            _bare_model_id(model_ref),
            provider=DeepSeekProvider(api_key=settings.api_key),
        )
        _agents[key] = Agent(model, output_type=output_type, retries=2)
    return _agents[key]


async def _spend_so_far() -> float:
    raw = await get_redis().get("llm:spend_usd")
    return float(raw) if raw else 0.0


async def _record_spend(cost_usd: float) -> float:
    return await RedisCache().incrbyfloat("llm:spend_usd", cost_usd)


def _cost(model_id: str, tokens_in: int, tokens_out: int, cache_read: int) -> float:
    price = _PRICING.get(model_id, _PRICING["deepseek-v4-flash"])
    miss_in = max(tokens_in - cache_read, 0)
    return (
        miss_in / 1_000_000 * price["in"]
        + cache_read / 1_000_000 * price["cache_in"]
        + tokens_out / 1_000_000 * price["out"]
    )


async def _get_cached(prompt_hash: str, model_id: str, schema_version: str) -> dict | None:
    cache = RedisCache()
    redis_key = f"llmcache:{prompt_hash}:{model_id}:{schema_version}"
    hit = await cache.get_json(redis_key)
    if hit is not None:
        return hit
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT response, tokens_in, tokens_out FROM llm_cache "
            "WHERE prompt_hash = $1 AND model = $2 AND schema_version = $3",
            prompt_hash,
            model_id,
            schema_version,
        )
    if row is None:
        return None

    result = {
        "response": json.loads(row["response"]) if isinstance(row["response"], str) else row["response"],
        "tokens_in": row["tokens_in"],
        "tokens_out": row["tokens_out"],
    }
    await cache.set_json(redis_key, result, ttl_s=get_settings().redis.llm_cache_ttl_s)
    return result


async def _store_cached(
    prompt_hash: str, model_id: str, schema_version: str, response: dict, tokens_in: int, tokens_out: int
) -> None:
    cache = RedisCache()
    redis_key = f"llmcache:{prompt_hash}:{model_id}:{schema_version}"
    await cache.set_json(
        redis_key,
        {"response": response, "tokens_in": tokens_in, "tokens_out": tokens_out},
        ttl_s=get_settings().redis.llm_cache_ttl_s,
    )
    async with acquire() as conn:
        await conn.execute(
            "INSERT INTO llm_cache (prompt_hash, model, schema_version, response, tokens_in, tokens_out) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (prompt_hash, model, schema_version) DO NOTHING",
            prompt_hash,
            model_id,
            schema_version,
            json.dumps(response),
            tokens_in,
            tokens_out,
        )


@llm_retry(max_attempts=3)
async def _call_agent(agent: Agent, user_prompt: str, system_prompt: str):
    return await agent.run(user_prompt, instructions=system_prompt)


async def complete_structured(
    *,
    tier: Tier,
    output_type: type[T],
    system_prompt: str,
    user_prompt: str,
    schema_version: str = "v1",
    stub_factory: Callable[[], T] | None = None,
) -> LLMResult:
    """The one entry point every agent uses to call an LLM.

    Cache and spend-ceiling checks happen here so no individual agent can
    accidentally skip them. In stub mode (`CM_LLM__STUB=true`, or no API key
    configured) `stub_factory` is called instead of touching the network —
    this is what lets `make demo` and the test suite run with zero
    credentials.
    """
    settings = get_settings().llm
    model_id = _bare_model_id(model_for_tier(tier))

    if settings.stub or not settings.api_key:
        if stub_factory is None:
            raise RuntimeError(
                f"LLM stub mode is active but no stub_factory was given for {output_type.__name__}"
            )
        return LLMResult(
            value=stub_factory(), model="stub", tokens_in=0, tokens_out=0, cost_usd=0.0,
            cache_hit=False, latency_ms=0,
        )

    prompt_hash = _hash_prompt(system_prompt, user_prompt, model_id, schema_version)

    cached = await _get_cached(prompt_hash, model_id, schema_version)
    if cached is not None:
        value = output_type.model_validate(cached["response"])
        cost = _cost(model_id, cached["tokens_in"], 0, cached["tokens_in"])  # fully cached read
        log.info("llm.cache_hit", model=model_id, schema=output_type.__name__)
        return LLMResult(
            value=value, model=model_id, tokens_in=cached["tokens_in"],
            tokens_out=cached["tokens_out"], cost_usd=cost, cache_hit=True, latency_ms=0,
        )

    spent = await _spend_so_far()
    if spent >= settings.max_spend_usd:
        raise BudgetExceededError(
            f"LLM spend ceiling reached: ${spent:.4f} spent, ceiling ${settings.max_spend_usd:.2f}"
        )

    agent = _get_agent(model_for_tier(tier), output_type)
    started = time.monotonic()
    result = await _call_agent(agent, user_prompt, system_prompt)
    latency_ms = int((time.monotonic() - started) * 1000)

    usage = result.usage
    tokens_in = usage.input_tokens or 0
    tokens_out = usage.output_tokens or 0
    cache_read = getattr(usage, "cache_read_tokens", 0) or 0
    cost = _cost(model_id, tokens_in, tokens_out, cache_read)
    total_spent = await _record_spend(cost)

    await _store_cached(
        prompt_hash, model_id, schema_version, result.output.model_dump(mode="json"), tokens_in, tokens_out
    )

    log.info(
        "llm.call", model=model_id, schema=output_type.__name__, tokens_in=tokens_in,
        tokens_out=tokens_out, cache_read=cache_read, cost_usd=round(cost, 6),
        total_spent_usd=round(total_spent, 4), latency_ms=latency_ms,
    )

    return LLMResult(
        value=result.output, model=model_id, tokens_in=tokens_in, tokens_out=tokens_out,
        cost_usd=cost, cache_hit=False, latency_ms=latency_ms,
    )
