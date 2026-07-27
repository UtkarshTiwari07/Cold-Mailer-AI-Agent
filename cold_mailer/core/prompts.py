"""Loads versioned prompt templates from `prompts/*.md`.

Kept as plain files rather than inline Python string constants so the actual
wording of each agent's instructions is ops-editable without a code change
or redeploy — matches the "configuration over code" spirit applied
elsewhere (YAML settings, `.env`). Cached in-process after first read since
the content must be byte-identical across calls for DeepSeek's prefix cache
to hit (see `core/llm.py`); restart the process to pick up an edit.
"""

from __future__ import annotations

from functools import cache

from cold_mailer.core.config import get_settings

# Bump when a prompt's wording changes meaningfully. Used as the cache key
# alongside prompt content itself, so `llm_cache` doesn't serve a response
# generated under an old, since-edited version of a prompt.
PROMPT_VERSION = "v1"


@cache
def load_prompt(name: str) -> str:
    path = get_settings().prompts_dir / f"{name}.md"
    return path.read_text().strip()
