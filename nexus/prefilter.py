"""Embedding-based candidate pre-filter.

Company description embeddings are computed once in Stage 0 and cached.
Per faculty run: one embedding call for the research profile, then a
cosine-similarity ranking over all companies (fractions of a second),
returning the indices of the top-N candidates for LLM scoring.
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence

import numpy as np

_WORD = re.compile(r"[A-Za-z]+")
_VOWEL = re.compile(r"[aeiou]", re.I)


def is_stub_description(text) -> bool:
    """True for a description that says nothing: blank, "-", or one of
    Capital IQ's short industry-code abbreviations ("Ofcs Clns Of Mdl D",
    "Sklld Nrsng Cr Fclt") - most of its words have no vowel after the
    first letter."""
    t = "" if text is None or (isinstance(text, float) and text != text) else str(text).strip()
    if t in ("", "-", "nan", "None"):
        return True
    if len(t) >= 60:
        return False
    words = [w for w in _WORD.findall(t) if len(w) >= 3]
    if not words:
        return True
    abbreviated = sum(1 for w in words if not _VOWEL.search(w[1:]))
    return abbreviated / len(words) >= 0.5


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


def company_embedding_text(name: str, description: Optional[str],
                           research_focus: Optional[str] = None) -> str:
    """Text used to represent a company in embedding space: name, Capital IQ
    description, and - where the sources know it - what it researches
    (trial conditions, OpenAlex topics, SBIR project titles)."""
    desc = company_profile_text(description, research_focus)
    return f"{name}. {desc}".strip()


def company_profile_text(description: Optional[str],
                         research_focus: Optional[str] = None) -> str:
    """Description plus research focus - what the alignment scorer reads."""
    def clean(v):
        return "" if is_stub_description(v) else str(v).strip()
    desc, focus = clean(description), clean(research_focus)
    return f"{desc}\n\nResearch focus: {focus}".strip() if focus else desc


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


def rank_by_weighted_queries(query_vectors, weights, company_vectors) -> List[float]:
    """For several queries with weights (a researcher's publication titles):
    each company's best weighted cosine similarity with any one query.
    Companies with no embedding get -1."""
    q = [(np.asarray(v, dtype=np.float32), float(w)) for v, w in zip(query_vectors, weights)
         if v is not None and w > 0]
    if not q:
        return [-1.0] * len(company_vectors)
    dim = len(q[0][0])
    Q = np.stack([v / (np.linalg.norm(v) or 1.0) for v, _ in q])          # titles x dim
    W = np.asarray([w for _, w in q], dtype=np.float32)
    have = [i for i, v in enumerate(company_vectors) if v is not None]
    out = np.full(len(company_vectors), -1.0, dtype=np.float32)
    if have:
        M = np.asarray([company_vectors[i] for i in have], dtype=np.float32).reshape(len(have), dim)
        M = M / np.maximum(np.linalg.norm(M, axis=1, keepdims=True), 1e-12)
        out[have] = ((M @ Q.T) * W).max(axis=1)
    return out.tolist()


def select_candidates(similarities: List[float], top_n: int) -> List[int]:
    """Indices of the top-N companies by similarity. top_n <= 0 -> all."""
    order = sorted(range(len(similarities)), key=lambda i: similarities[i], reverse=True)
    if top_n and top_n > 0:
        return order[:top_n]
    return order
