"""Text embeddings for evidence retrieval.

`fastembed` (ONNX, local, CPU, free) is the real backend and is what
DESIGN.md recommends for production, but it's a heavy optional dependency
(the `full` extra) — not everyone building or testing this pipeline wants to
download an ONNX runtime and model weights. When it isn't installed, this
falls back to a deterministic hashing-trick bag-of-words vector: not a
real embedding model, but stable, dependency-free, and good enough that
cosine similarity still meaningfully ranks "this paragraph mentions
Kubernetes" above "this paragraph is about the cafeteria menu" for the
MVP's evidence-lookup needs. Swap in fastembed by installing the `full`
extra — no caller code changes, since both paths return the same shape.
"""

from __future__ import annotations

import hashlib
import math

_DIM = 384  # matches bge-small-en-v1.5 and the `evidence.embedding vector(384)` column

_fastembed_model = None


def _try_load_fastembed():
    global _fastembed_model
    if _fastembed_model is not None:
        return _fastembed_model
    try:
        from fastembed import TextEmbedding

        _fastembed_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    except Exception:
        _fastembed_model = False  # sentinel: "tried, unavailable"
    return _fastembed_model


def embed(text: str) -> list[float]:
    model = _try_load_fastembed()
    if model:
        return list(next(model.embed([text])))
    return _hashing_embed(text)


def _hashing_embed(text: str) -> list[float]:
    vec = [0.0] * _DIM
    for token in text.lower().split():
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        vec[h % _DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    # strict=True: mismatched vector dimensions are a real bug (comparing
    # embeddings from two different models/dims), not something to
    # silently truncate and return a meaningless number for.
    num = sum(x * y for x, y in zip(a, b, strict=True))
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(y * y for y in b)) or 1.0
    return num / (da * db)
