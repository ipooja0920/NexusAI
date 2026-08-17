"""AI scoring (alignment / partnership / funding) and component score math.

Prompts and formulas carried over from ProgramTesting9, with one
improvement: the company's business description is included in the
alignment prompt so small/obscure companies are judged on what they
actually do rather than on the model's memory of their name.
"""
from __future__ import annotations

import json
import math
import time
from typing import List, Optional, Tuple

STD_NA_REASON = "No alignment information available"

ALIGNMENT_SYSTEM_PROMPT = (
    "You are an analyst evaluating how well companies align with a university research program. "
    "Your goal is to rate how relevant a company's activities are to the research described.\n\n"
    "Use the following scoring scale:\n"
    "1-2: No meaningful connection\n"
    "3-4: Weak or indirect connection\n"
    "5-6: Moderate relevance; some related areas\n"
    "7-8: Strong relevance; clear overlap in focus or technology\n"
    "9: Exceptional match; the company's work directly overlaps with the research focus\n\n"
    "Consider:\n"
    "- Scientific mechanisms\n"
    "- Technologies or platforms\n"
    "- Disease or application areas\n"
    "- Research methods or capabilities\n\n"
    "Focus on specific alignment with the research described. A company "
    "description is provided when available; ground your judgment in it.\n\n"
    "Return ONLY valid JSON with fields: "
    '{"company": <exact name>, "score": 1-9 integer OR "NA", "reason": string <= 40 words}.\n'
    "RULE: If there is no reliable information about the company's alignment, "
    'set score to "NA" and reason to "No alignment information available".'
)

PARTNERSHIP_SYSTEM_PROMPT = (
    "You are an analyst evaluating how likely a company is to want to partner with a university "
    "research program.\n\n"
    "Use the following scoring scale:\n"
    "1-2: Very unlikely - no history of academic engagement, closed R&D culture\n"
    "3-4: Unlikely - limited or superficial academic ties\n"
    "5-6: Moderate - some evidence of academic interest or collaboration\n"
    "7-8: Likely - active academic partnerships, sponsored research, or university hiring pipeline\n"
    "9: Very likely - strong track record of university collaborations, open innovation programs, "
    "or dedicated academic liaison programs\n\n"
    "Consider:\n"
    "- History of sponsored research or academic collaborations\n"
    "- R&D-driven company culture\n"
    "- Publication activity or conference participation\n"
    "- Startup/early-stage vs. established company dynamics\n"
    "- Industry sector norms for academic engagement\n\n"
    "Return ONLY valid JSON with fields: "
    '{"company": <exact name>, "score": 1-9 integer OR "NA", "reason": string <= 40 words}.\n'
    "RULE: If there is insufficient public information to assess, "
    'set score to "NA" and reason to "No alignment information available".'
)

FUNDING_SYSTEM_PROMPT = (
    "You are an analyst evaluating how likely a company is to have budget available to fund "
    "outside academic research.\n\n"
    "Use the following scoring scale:\n"
    "1-2: Very unlikely - pre-revenue, distressed, or no R&D budget history\n"
    "3-4: Unlikely - limited R&D spend, budget constraints evident\n"
    "5-6: Moderate - stable revenue but R&D prioritization unclear\n"
    "7-8: Likely - meaningful R&D budget, history of sponsored research or grants\n"
    "9: Very likely - large R&D spend, government contracts, or dedicated external research "
    "funding programs\n\n"
    "Consider:\n"
    "- Company revenue and financial stability\n"
    "- R&D expenditure as a proportion of revenue\n"
    "- History of sponsored research agreements or grants\n"
    "- Government or foundation funding relationships\n"
    "- Company size and growth stage\n\n"
    "Return ONLY valid JSON with fields: "
    '{"company": <exact name>, "score": 1-9 integer OR "NA", "reason": string <= 40 words}.\n'
    "RULE: If there is insufficient public information to assess, "
    'set score to "NA" and reason to "No alignment information available".'
)


# ======================================================================
# LLM calls
# ======================================================================

def _parse_score_reason(raw: str) -> Tuple[str, str]:
    try:
        data = json.loads(raw)
        score = data.get("score")
        reason = str(data.get("reason", "")).strip()
        if isinstance(score, int):
            score = str(max(1, min(9, score)))
        elif isinstance(score, str) and score.strip().upper() == "NA":
            score = "NA"
        else:
            score = "NA"
        if score == "NA":
            reason = STD_NA_REASON
        if not reason:
            reason = "No reason provided"
        return score, reason
    except Exception:
        return "NA", STD_NA_REASON


def _chat(client, model: str, system_prompt: str, user_prompt: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _with_retries(fn, max_retries: int, rate_limit: float) -> Tuple[str, str]:
    for attempt in range(1, max_retries + 1):
        try:
            score, reason = _parse_score_reason(fn())
            if rate_limit > 0:
                time.sleep(rate_limit)
            return score, reason
        except Exception:
            if attempt < max_retries:
                time.sleep(1.0 * attempt)
    return "NA", STD_NA_REASON


def score_alignment(client, model: str, company: str, description: Optional[str],
                    research_profile: str, max_retries: int = 3,
                    rate_limit: float = 0.0) -> Tuple[str, str]:
    desc_part = f"Company description: {description}\n" if description and description != "-" else ""
    user_prompt = (
        f"Research program:\n{research_profile}\n\n"
        f"Company: {company}\n{desc_part}"
        f"Respond with JSON only."
    )
    return _with_retries(
        lambda: _chat(client, model, ALIGNMENT_SYSTEM_PROMPT, user_prompt),
        max_retries, rate_limit,
    )


def score_secondary(client, model: str, company: str, system_prompt: str,
                    description: Optional[str] = None,
                    max_retries: int = 3, rate_limit: float = 0.0) -> Tuple[str, str]:
    desc_part = f"Company description: {description}\n" if description and description != "-" else ""
    user_prompt = f"Company: {company}\n{desc_part}Respond with JSON only."
    return _with_retries(
        lambda: _chat(client, model, system_prompt, user_prompt),
        max_retries, rate_limit,
    )


# ======================================================================
# Component score math (deterministic, no API)
# ======================================================================

def parse_revenue(raw) -> Optional[float]:
    """'66,442.9' -> 66442.9 ; '-' / blank / junk -> None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-") or s.lower() == "nan":
        return None
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def parse_employees(raw) -> float:
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if s in ("", "-") or s.lower() == "nan":
        return 0.0
    try:
        return float(s.replace(",", ""))
    except Exception:
        return 0.0


def distance_score(miles: Optional[float], decay: float) -> float:
    if miles is None or miles == 0.0:
        return 0.0
    try:
        return math.exp(-float(miles) / decay)
    except Exception:
        return 0.0


def employee_score(employees: float, cap: float) -> float:
    return min(1.0, max(0.0, employees / cap))


def revenue_score(value: Optional[float], cfg: dict) -> float:
    lo0, lo1 = cfg["rev_low_zero"], cfg["rev_low_full"]
    hi1, hi0 = cfg["rev_high_full"], cfg["rev_high_zero"]
    if value is None:
        return cfg["rev_missing_score"]
    if value <= lo0:
        return 0.0
    if value < lo1:
        return value / lo1
    if value <= hi1:
        return 1.0
    if value < hi0:
        return (hi0 - value) / (hi0 - hi1)
    return 0.0


def normalize(values: List[Optional[float]]) -> List[float]:
    """Min-max normalize; None -> 0.0. Constant series -> all 0.0."""
    nums = [v for v in values if v is not None]
    if not nums:
        return [0.0] * len(values)
    lo, hi = min(nums), max(nums)
    if hi - lo <= 0:
        return [0.0 if v is None else 0.0 for v in values]
    return [0.0 if v is None else (v - lo) / (hi - lo) for v in values]
