"""Agent 6 — Personalization / Candidate Profile Memory.

The simplest agent in the system by design: a typed, cached read of
`profile/utkarsh.yaml`. Kept as its own module (not inlined config) so it's
the one file a non-engineer edits directly, and so A5/A7 have a single
`load_profile()` call to depend on rather than reaching into a YAML file
themselves.
"""

from __future__ import annotations

from functools import lru_cache

import yaml

from cold_mailer.contracts.a6_profile import CandidateProfile
from cold_mailer.core.config import get_settings


@lru_cache
def load_profile() -> CandidateProfile:
    path = get_settings().profile_path
    data = yaml.safe_load(path.read_text())
    return CandidateProfile.model_validate(data)


@lru_cache
def profile_system_prefix() -> str:
    """The candidate profile serialized once, meant to be appended to the
    STABLE part (system prompt) of any agent that needs it — never the
    variable per-company user prompt. The profile never changes within a
    run, so putting it in the system message is what makes DeepSeek's
    prefix cache actually hit across every A5/A7 call this run makes (see
    `core/llm.py`'s module docstring for why this ordering matters)."""
    profile = load_profile()
    return "Candidate profile:\n" + profile.model_dump_json(indent=2)
