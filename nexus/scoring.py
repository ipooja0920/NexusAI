"""AI scoring (alignment / partnership / funding) and component score math.

Prompts and formulas carried over from ProgramTesting9, with one
improvement: the company's business description is included in the
alignment prompt so small/obscure companies are judged on what they
actually do rather than on the model's memory of their name.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from typing import List, Optional, Tuple

STD_NA_REASON = "No alignment information available"

ALIGNMENT_SYSTEM_PROMPT = (
    "You are an analyst evaluating how well companies align with a university research program. "
    "Your goal is to rate how relevant a company's activities are to the research described.\n\n"
    "Judge first by HOW the researcher works - the methods, tools and approaches in "
    "their keywords and description (what they measure, build, test or use) - and only "
    "then by WHAT condition, body part or patient group they study. A company that "
    "treats the same condition with a different kind of product does not match as "
    "well as one whose products the researcher could use, test or develop.\n\n"
    "Use the following scoring scale:\n"
    "9: the company's products or technology are the kind of tools or methods the "
    "researcher works with, applied to the same problem\n"
    "7-8: strong overlap in methods or technology - the company's products could be "
    "used, tested or developed in the researcher's work - even if the application "
    "differs somewhat\n"
    "5-6: the same condition, body part or patient group but a different approach "
    "(for example a surgical implant, drug, biologic or cell therapy when the "
    "researcher measures movement or develops rehabilitation), or related methods "
    "in a different area\n"
    "3-4: weak or indirect connection - the description names something specific "
    "the researcher could use or study (a related method, tool, condition or body "
    "part), but only loosely\n"
    "1-2: no meaningful connection - an unrelated company scores 1-2, not NA. If the "
    "description names none of the researcher's methods, tools, conditions, body "
    "parts or patient groups, the score is 1-2, not 3 - general medical, drug, "
    "insurance, lab or animal-health work with no such link is 1-2\n\n"
    "Consider:\n"
    "- The researcher's keywords and the problems their research addresses\n"
    "- Scientific mechanisms\n"
    "- Technologies or platforms\n"
    "- Disease, condition or application areas\n"
    "- Research methods or capabilities, including testing or validating the "
    "company's products in the researcher's lab\n"
    "- The nature of the research (applied / translational / clinical work fits "
    "companies with products in that area) and the industry sectors and "
    "interests the researcher names\n\n"
    "Focus on specific alignment with the research described.\n\n"
    "GROUNDING RULES - follow them exactly:\n"
    "- Use ONLY the company information in this message (name, industry, "
    "description). Do not use outside knowledge about the company, its products, "
    "programs or partnerships - even for a well-known company, judge only from "
    "the text given.\n"
    "- Your reason must refer to what the description actually says.\n"
    "- Use NA only if the description is missing or says nothing about what the "
    "company does; a company whose work is simply unrelated scores 1-2.\n\n"
    "Return ONLY valid JSON with fields: "
    '{"company": <exact name>, "score": 1-9 integer OR "NA", "reason": string <= 40 words}.\n'
    "RULE: If there is no reliable information about the company's alignment in the "
    'text given, set score to "NA" and reason to "No alignment information available".'
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
    "- Industry sector norms for academic engagement\n"
    "- The university track record given (past and current university partners on "
    "federal SBIR/STTR awards and clinical trials, university research it funded "
    "or co-wrote)\n\n"
    "GROUNDING RULES - follow them exactly:\n"
    "- Use ONLY the company description and the university track record given in "
    "this message. Do not use outside knowledge (programs, collaborations, "
    "incubators or reputations you have heard of) - even for a well-known company.\n"
    "- Your reason must refer to what the description or track record says.\n"
    "- A small or private company is judged on what is given (an R&D-driven device or "
    "biotech developer is more likely to partner than a distributor or a single "
    "clinic) - do not answer NA just because the company is obscure.\n\n"
    "Return ONLY valid JSON with fields: "
    '{"company": <exact name>, "score": 1-9 integer OR "NA", "reason": string <= 40 words}.\n'
    "RULE: Only if no company description is given and you know nothing about the "
    'company, set score to "NA" and reason to "No alignment information available".'
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


# API calls and tokens used, so a sample run can say what a full run costs
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
_USAGE_LOCK = threading.Lock()


def reset_usage() -> None:
    with _USAGE_LOCK:
        for k in USAGE:
            USAGE[k] = 0


def _chat(client, model: str, system_prompt: str, user_prompt: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    usage = getattr(resp, "usage", None)
    with _USAGE_LOCK:
        USAGE["calls"] += 1
        if usage is not None:
            USAGE["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
            USAGE["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
    return (resp.choices[0].message.content or "").strip()


RATE_LIMIT_WAITS = 8      # extra tries when OpenAI says "slow down"


def _with_retries(fn, max_retries: int, rate_limit: float) -> Tuple[str, str]:
    attempt, waits = 1, 0
    while attempt <= max_retries:
        try:
            score, reason = _parse_score_reason(fn())
            if rate_limit > 0:
                time.sleep(rate_limit)
            return score, reason
        except Exception as e:
            # Many calls run in parallel: a rate-limit error means "wait",
            # not "this company cannot be scored" - back off without using
            # up a real attempt
            if "ratelimit" in type(e).__name__.lower() and waits < RATE_LIMIT_WAITS:
                waits += 1
                time.sleep(min(60.0, 5.0 * waits))
                continue
            if attempt < max_retries:
                time.sleep(1.0 * attempt)
            attempt += 1
    return "NA", STD_NA_REASON


def score_alignment(client, model: str, company: str, description: Optional[str],
                    research_profile: str, max_retries: int = 3,
                    rate_limit: float = 0.0, industry: Optional[str] = None) -> Tuple[str, str]:
    desc_part = f"Company description: {description}\n" if description and description != "-" else ""
    ind_part = f"Industry: {industry}\n" if industry else ""
    user_prompt = (
        f"Research program:\n{research_profile}\n\n"
        f"Company: {company}\n{ind_part}{desc_part}"
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


JUSTIFICATION_SYSTEM_PROMPT = (
    "You write the justification column of a faculty-to-company match list for a "
    "university research office. Given a researcher's profile and a list of FACTS "
    "about a company, explain in 2-3 sentences (at most 80 words) why this company "
    "is a match for this researcher. Name the specific overlap: which of the "
    "researcher's keywords, problems or methods - or, where given, a listed "
    "publication - the company's products, technologies "
    "or conditions (as stated in the company profile) touch. Then mention the "
    "practical factors that help or hurt (distance, size, money available, track "
    "record with universities, organisation type, parent company).\n\n"
    "Begin with the verdict given in the FACTS (\"Strong fit\", \"Moderate fit\", "
    "\"Weak fit\" or \"Fit unclear\"), then a colon. For a weak fit say plainly that the "
    "overlap is weak or absent - do not present it as an alignment.\n\n"
    "GROUNDING RULES - follow them exactly:\n"
    "- Every statement must come from the researcher profile or the FACTS given. "
    "Do not use outside knowledge about the company.\n"
    "- Do not mention any university, program, partnership, product, number, year or "
    "amount that is not written in the FACTS.\n"
    "- If the FACTS say no university track record or no funding record, say that - "
    "do not guess.\n"
    "- Do not name any people. If the overlap is weak, say so plainly.\n\n"
    "VOICE: write about the researcher as \"the researcher\" or \"you\"/\"your\". "
    "Never write as the researcher - no \"I\", \"my\", \"me\", \"we\" or \"our\" - "
    "even when the researcher's profile is written in the first person.\n\n"
    'Return ONLY valid JSON: {"justification": string}.'
)

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_UNI = re.compile(r"\b(University of [A-Z][\w&.' -]*?|[A-Z][\w&.'-]*(?: [A-Z][\w&.'-]*)* "
                  r"(?:University|College|Institute|Hospital))\b")


def _numbers(text: str) -> set:
    out = set()
    for m in _NUM.findall(text or ""):
        try:
            v = float(m.replace(",", ""))
        except ValueError:
            continue
        out.add(round(v, 1))
        out.add(round(v))
    return out


def ungrounded(text: str, source: str) -> list:
    """Figures and university names in `text` that are not in `source` -
    the automatic check that a justification invents nothing. Scores out of
    9 (1-9) are allowed: they are what the scale is."""
    bad = []
    have = _numbers(source)
    for m in _NUM.findall(text or ""):
        v = float(m.replace(",", ""))
        if v in have or round(v, 1) in have or round(v) in have or (v.is_integer() and 1 <= v <= 9):
            continue
        bad.append(m)
    low = (source or "").lower()
    for m in _UNI.findall(text or ""):
        if m.lower().strip() not in low:
            bad.append(m)
    return bad


# "I" as a numeral is not first person: Phase I, Type I, "oxLink I and II" ...
_FIRST_PERSON = re.compile(
    r"(?<!Phase )(?<!Type )(?<!Class )(?<!Stage )(?<!Tier )(?<!Part )(?<!Grade )"
    r"(?<!Level )(?<!Series )\bI\b(?!-)(?!\s*(?:and|or|,|/|&)\s*II\b)|\b[Mm]y\b|\b[Mm]yself\b|\b[Mm]ine\b|\bme\b|"
    r"\b[Ww]e\b|\b[Oo]ur\b|\b[Oo]urs\b|\bus\b")


def first_person(text: str) -> list:
    """First-person words in a justification ("my research", "I develop"):
    the justification speaks about the researcher, never as them."""
    return _FIRST_PERSON.findall(text or "")


def write_justification(client, model: str, research_profile: str, company: str,
                        facts: str, max_retries: int = 3) -> Optional[str]:
    """2-3 sentences on why this company matches this researcher, built only
    from the facts given. Each answer is checked: a figure or a university
    name that is not in the researcher profile or the facts sends it back
    once with the problem named; if it still invents, None is returned and
    the caller writes a plain justification from the data instead. The
    same one rewrite is used for an answer written as the researcher
    ("my work"); if it is still in the first person, None as well."""
    base = (f"Researcher:\n{research_profile}\n\nCompany: {company}\nFACTS:\n"
            f"{facts}\n\nRespond with JSON only.")
    source = f"{research_profile}\n{company}\n{facts}"
    user_prompt, rewrites = base, 0
    for attempt in range(1, max_retries + 1):
        try:
            raw = _chat(client, model, JUSTIFICATION_SYSTEM_PROMPT, user_prompt)
            text = str(json.loads(raw).get("justification", "")).strip()
            if text:
                bad = ungrounded(text, source)
                voice = first_person(text)
                if not bad and not voice:
                    return text
                if rewrites >= 1:
                    return None
                rewrites += 1
                problems = []
                if bad:
                    problems.append("it mentioned things that are not in the FACTS: "
                                    f"{', '.join(bad)} - use only the FACTS")
                if voice:
                    problems.append("it was written as the researcher (" +
                                    ", ".join(sorted(set(voice))) + ") - write about "
                                    "the researcher as \"the researcher\" or \"you\"")
                user_prompt = (base + "\n\nRewrite your previous answer: "
                               + "; ".join(problems) + ".")
                continue
        except Exception:
            pass
        if attempt < max_retries:
            time.sleep(1.0 * attempt)
    return None


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
    """exp(-miles/decay). Unknown distance scores 0.

    Zero miles is NOT unknown - it is the best possible answer, and it used
    to be lumped in with "no address" and scored 0.0. A company sitting in
    Storrs scored the same on proximity as one we could not locate at all,
    which inverted the entire point of a 25% distance weight. Only None
    (and unparseable junk) means unknown.
    """
    if miles is None:
        return 0.0
    try:
        m = float(miles)
    except (TypeError, ValueError):
        return 0.0
    if m != m or m < 0:                       # NaN or nonsense
        return 0.0
    return math.exp(-m / decay)


def employee_score(employees: float, cap: float) -> float:
    return min(1.0, max(0.0, employees / cap))


def revenue_score(value: Optional[float], cfg: dict) -> float:
    """SIZE fit from revenue ($USDmm), 0.0-1.0.

    The target is an established company with a budget that still needs
    outside research: full marks from sweet_lo ($100m) to sweet_hi ($1bn),
    then a taper to big_floor at big_hi ($5bn), where it stays - the giants
    rarely need UConn, but they do sometimes sponsor, so they are not zeroed.
    Smaller companies get small_score / mid_score here and earn their place
    through the funding score instead. No figure -> missing_score (neutral).

    The previous curve peaked at $30-60m and reached ZERO at $1bn, so a
    $500m company scored below a $40m one - the opposite of the "at least
    $100m" rule.
    """
    v = _num(value)
    if v is None:
        return cfg["missing_score"]
    if v < cfg["small_max"]:
        return cfg["small_score"]
    if v < cfg["sweet_lo"]:
        return cfg["mid_score"]
    if v <= cfg["sweet_hi"]:
        return 1.0
    if v < cfg["big_hi"]:
        span = cfg["big_hi"] - cfg["sweet_hi"]
        return 1.0 - (1.0 - cfg["big_floor"]) * (v - cfg["sweet_hi"]) / span
    return cfg["big_floor"]


def _num(value) -> Optional[float]:
    """A float, or None for blank / NaN / "-" / junk."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def log_score(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """Position of `value` between `lo` and `hi` on a LOG scale, 0.0-1.0.

    Returns None when there is no figure, which is different from 0.0:
    0.0 means "we know it is tiny", None means "we have no record". The
    caller decides what absence is worth - see funding_score.

    Log rather than linear because these figures span five orders of
    magnitude: $50k at the 1st percentile, $261m at the 99th. Scored
    linearly against the maximum, the MEDIAN company reads 0.011 and the
    whole component stops discriminating.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v <= 0:                      # NaN, zero or negative
        return None
    if v <= lo:
        return 0.0
    if v >= hi:
        return 1.0
    return (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))


def _year(value) -> Optional[int]:
    """A four-digit year, or None. Tolerates NaN, floats and strings."""
    if value is None:
        return None
    try:
        y = int(float(value))
    except (TypeError, ValueError):
        return None
    return y if 1900 <= y <= 2100 else None


def recency_factor(years, cfg: dict) -> float:
    """How much to trust a funding figure, given WHEN it was reported.

    A dollar figure alone does not say whether a company can fund research
    now. Money raised in 2019 was spent in 2019. This scales the activity
    signal by how fresh the most recent evidence is, so a company that
    raised last year outranks one that raised four years ago on the same
    amount.

    The most recent year across all sources wins: a company still filing
    Form D is current whether or not its SBIR awards stopped in 2011.
    """
    known = [y for y in (_year(v) for v in years) if y is not None]
    if not known:
        return cfg["stale"]          # no date at all - treat as old, not fresh
    latest = max(known)
    if latest >= cfg["fresh_year"]:
        return cfg["fresh"]
    if latest >= cfg["fresh_year"] - 1:
        return cfg["recent"]
    return cfg["stale"]


def _nonblank(value) -> bool:
    """True when a text cell actually holds something. NaN is truthy in
    Python, so a plain `if value` reports every blank cell as populated."""
    if value is None:
        return False
    if isinstance(value, float) and value != value:      # NaN
        return False
    return str(value).strip() not in ("", "-", "nan", "None")


def money_score(signals: dict, cfg: dict, oa_funded_recent: bool = False,
                formd_raised_small: bool = False) -> tuple:
    """Is there money available NOW? -> (score 0.0-1.0, the signal that won).

    `signals` is {name: (amount in $ millions, year it describes)} for
    formd_recent, cash_total, rd_expense and sbir_active. Each is placed on
    its own log scale from config and aged by its year: an old figure is
    pulled TOWARDS neutral, never below it - old money is a weaker claim,
    not a mark against.

    The BEST signal counts - max, not a sum: a company with both cash in
    the bank and a recent raise has the same answer twice, not a bigger one,
    and a company should not lose for having only one kind of record.

    oa_funded_recent (it paid for university papers this year or last) is
    full marks on its own. With no signal at all the score is neutral
    (missing_score) - plus a small bonus when the company has raised
    private money at some point and is small or of unknown size.
    """
    neutral = cfg["missing_score"]
    scored = []
    for name, (amount, year) in signals.items():
        lim = cfg["signals"][name]
        s = log_score(amount, lim["lo"], lim["hi"])
        if s is None:
            continue
        factor = recency_factor([year] if year is not None else [], cfg["recency"])
        scored.append((neutral + (s - neutral) * factor if s > neutral else s, name))
    if oa_funded_recent:
        scored.append((1.0, "oa_funded"))
    if not scored:
        bonus = cfg["formd_raised_bonus"] if formd_raised_small else 0.0
        return round(min(1.0, neutral + bonus), 4), None
    best = max(scored)
    return round(min(1.0, max(0.0, best[0])), 4), best[1]


def academic_adjustment(flags: dict, ct_active, cfg: dict) -> tuple:
    """Signed nudge to the partnership score from the academic track record
    -> (adjustment, [names of the signals that applied]).

    Past ties (and having paid for university research) count FOR a
    company: it knows how a sponsored-research agreement works. A CURRENT
    university partner counts against it: less room for UConn. Each side is
    capped, so one company with every kind of tie cannot swamp the AI
    partnership judgement it adjusts.
    """
    bonus = sum(v for k, v in cfg["bonus"].items() if flags.get(k))
    penalty = sum(v for k, v in cfg["penalty"].items() if flags.get(k))
    used = [k for k in list(cfg["bonus"]) + list(cfg["penalty"]) if flags.get(k)]
    adj = min(bonus, cfg["bonus_cap"]) + max(penalty, cfg["penalty_cap"])
    n_active = _num(ct_active) or 0
    if cfg.get("ct_active_penalty") and n_active > cfg.get("ct_active_above", 0):
        adj += cfg["ct_active_penalty"]
        used.append("ct_trials_active")
    return round(adj, 4), used


def normalize(values: List[Optional[float]]) -> List[float]:
    """Min-max normalize; None -> 0.0. Constant series -> all 0.0."""
    nums = [v for v in values if v is not None]
    if not nums:
        return [0.0] * len(values)
    lo, hi = min(nums), max(nums)
    if hi - lo <= 0:
        return [0.0 if v is None else 0.0 for v in values]
    return [0.0 if v is None else (v - lo) / (hi - lo) for v in values]
