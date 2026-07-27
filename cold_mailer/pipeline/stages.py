"""Task-kind dispatch registry.

Each agent registers its handler under a `kind` string via `@task_handler`.
The worker imports this module (which imports every agent module, causing
registration to run) and looks up handlers by kind at claim time — so adding
a new stage never means touching `worker.py`, only adding
`@task_handler("new_kind")` to the new agent function.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from cold_mailer.pipeline.state_machine import Task

Handler = Callable[[Task], Awaitable[None]]

_REGISTRY: dict[str, Handler] = {}


def task_handler(kind: str) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        if kind in _REGISTRY:
            raise ValueError(f"Duplicate task handler registration for kind={kind!r}")
        _REGISTRY[kind] = fn
        return fn

    return decorator


def get_handler(kind: str) -> Handler | None:
    return _REGISTRY.get(kind)


def registered_kinds() -> list[str]:
    return sorted(_REGISTRY)


@task_handler("noop")
async def _noop(task: Task) -> None:
    """Permanent diagnostic handler: proves claim -> dispatch -> complete
    works end-to-end without depending on any agent being implemented, and
    doubles as a worker liveness check in production."""


def load_all_agents() -> None:
    """Import every agent module so its @task_handler registrations run.
    Deferred (not a top-level import in this file) so a missing optional
    dependency in one agent doesn't break the registry for every other one —
    each import is wrapped and logged rather than raised.
    """
    import importlib

    from cold_mailer.core.logging import get_logger

    log = get_logger(component="stages")
    modules = [
        "cold_mailer.agents.company_research",  # A1-A4 orchestrator: 'research_company'
        "cold_mailer.agents.a5_fit",  # 'synthesize_fit'
        "cold_mailer.agents.a7_generate",  # 'generate_draft'
        "cold_mailer.agents.a8_deliver",  # 'send_message'
        "cold_mailer.agents.a10_triage",  # 'triage_inbox'
    ]
    for mod in modules:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # pragma: no cover - defensive
            log.error("stages.agent_import_failed", module=mod, error=str(exc))
