"""Embedding-based candidate pre-filter.

Company description embeddings are computed once in Stage 0 and cached.
Per faculty run: one embedding call for the research profile, then a
cosine-similarity ranking over all companies (fractions of a second),
returning the indices of the top-N candidates for LLM scoring.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np


def embed_text(client, model: str, text: str, caches=None) -> Optional[list]:
    """Embed one text, using the persistent cache when available."""
    text = (text or "").strip()
    if not text:
        return None
    if caches is not None:
        cached = caches.get_embedding(text)
        if cached is not None:
            return cached
    resp = client.embeddings.create(model=model, input=text[:8000])
    vec = resp.data[0].embedding
    if caches is not None:
        caches.set_embedding(text, vec)
    return vec


def company_embedding_text(name: str, description: Optional[str]) -> str:
    """Text used to represent a company in embedding space."""
    desc = (description or "").strip()
    if desc in ("", "-", "nan"):
        desc = ""
    return f"{name}. {desc}".strip()


def rank_by_similarity(profile_vector: Sequence[float],
                       company_vectors: List[Optional[Sequence[float]]]) -> List[float]:
    """Cosine similarity of the faculty profile against every company.
    Companies with no embedding get similarity -1 (ranked last, not lost)."""
    p = np.asarray(profile_vector, dtype=np.float32)
    p_norm = np.linalg.norm(p)
    sims: List[float] = []
    for vec in company_vectors:
        if vec is None:
            sims.append(-1.0)
            continue
        v = np.asarray(vec, dtype=np.float32)
        denom = p_norm * np.linalg.norm(v)
        sims.append(float(np.dot(p, v) / denom) if denom > 0 else -1.0)
    return sims


def select_candidates(similarities: List[float], top_n: int) -> List[int]:
    """Indices of the top-N companies by similarity. top_n <= 0 -> all."""
    order = sorted(range(len(similarities)), key=lambda i: similarities[i], reverse=True)
    if top_n and top_n > 0:
        return order[:top_n]
    return order
