"""AI-written company profile: the Capital IQ description enriched with
what the other sources say the company works on.

Sources (from the combined file):
  * Capital IQ business description        - the starting point, always
  * SBIR/STTR award titles and abstracts   - what it proposed to build
  * ClinicalTrials.gov conditions and treatment types (drug, device, ...)
  * OpenAlex research topics

The abstracts are the richest and the hardest to use as they are: a median
of ~5,000 characters per company (up to 32,000), repetitive across Phase I
and II, and sometimes naming researchers. So the model condenses all of it
into ~150 words of plain fact, with no people's names. Companies with none
of the extra sources keep their Capital IQ description unchanged - there is
nothing to add. Profiles are cached by their input text, so a re-run only
pays for companies whose source text changed.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

PROFILE_VERSION = "v1"
TITLES_MAX = 1500          # characters of SBIR titles given to the model
ABSTRACTS_MAX = 6000       # characters of SBIR abstracts (newest first)

PROFILE_SYSTEM_PROMPT = (
    "You are a company profile writer. From the source material given, write a "
    "short factual profile of the company that a university research office will "
    "use to judge which faculty research it fits.\n\n"
    "Write 100-170 words of plain prose (no headings, no bullet points) covering, "
    "where the sources say so:\n"
    "- what the company does and its products or services\n"
    "- its technologies, platforms and research methods\n"
    "- the diseases, conditions, body systems or application areas it works on\n"
    "- the kinds of interventions it develops (device, drug, biologic, diagnostic, "
    "software, ...)\n\n"
    "Rules: use ONLY the source material - never add outside knowledge or guesses. "
    "Do not name any people. Do not mention dollar amounts, funding agencies, award "
    "or grant programs, or numbers of trials. If the sources disagree, prefer the "
    "Capital IQ description. Output only the profile text."
)


def _clean(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    s = str(v).strip()
    return "" if s in ("-", "nan", "None") else s


def has_sources(row: dict) -> bool:
    """True when anything beyond the Capital IQ description is known."""
    return any(_clean(row.get(k)) for k in
               ("sbir_award_titles", "sbir_abstracts", "ct_conditions",
                "ct_treatment_types", "oa_topics"))


def source_text(name: str, description, row: dict) -> str:
    """The material the model condenses, labelled by source."""
    parts = [f"Company: {name}"]
    d = _clean(description)
    parts.append(f"Capital IQ description: {d or '(none)'}")
    if _clean(row.get("oa_topics")):
        parts.append(f"Research topics of its publications (OpenAlex): {_clean(row['oa_topics'])}")
    if _clean(row.get("ct_conditions")):
        parts.append(f"Conditions studied in its clinical trials: {_clean(row['ct_conditions'])}")
    if _clean(row.get("ct_treatment_types")):
        parts.append(f"Treatment types in its clinical trials: {_clean(row['ct_treatment_types'])}")
    if _clean(row.get("sbir_award_titles")):
        parts.append("SBIR/STTR project titles (newest first): "
                     + _clean(row["sbir_award_titles"])[:TITLES_MAX])
    if _clean(row.get("sbir_abstracts")):
        parts.append("SBIR/STTR project abstracts (newest first, may be cut short): "
                     + _clean(row["sbir_abstracts"])[:ABSTRACTS_MAX])
    return "\n\n".join(parts)


def cache_key(model: str, text: str) -> str:
    return f"{PROFILE_VERSION}|{model}|{text}"


def write_profile(client, model: str, text: str, max_retries: int = 3) -> Optional[str]:
    import time
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": PROFILE_SYSTEM_PROMPT},
                          {"role": "user", "content": text}])
            out = (resp.choices[0].message.content or "").strip()
            if out:
                return out
        except Exception:
            pass
        if attempt < max_retries:
            time.sleep(1.0 * attempt)
    return None


def build_profiles(client, model: str, jobs: dict, caches, workers: int = 16,
                   max_retries: int = 3,
                   progress: Callable[[int, int], None] = None) -> dict:
    """jobs: {row index: source text} -> {row index: profile or None}.
    Cached profiles are reused; the rest are written `workers` at a time."""
    out, todo = {}, {}
    for i, text in jobs.items():
        hit = caches.get_profile(cache_key(model, text))
        if hit:
            out[i] = hit
        else:
            todo[i] = text
    if not todo:
        return out
    lock = threading.Lock()
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(write_profile, client, model, t, max_retries): i
                   for i, t in todo.items()}
        for fut in as_completed(futures):
            i = futures[fut]
            prof = fut.result()
            out[i] = prof
            with lock:
                if prof:
                    caches.set_profile(cache_key(model, todo[i]), prof)
                done += 1
                if progress:
                    progress(done, len(todo))
    return out
