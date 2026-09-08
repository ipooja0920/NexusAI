"""Company-name matching between Capital IQ and external datasets.

Capital IQ spells a company one way ("Aderis Pharmaceuticals, Inc."); an SEC
filing, a patent assignment or an OpenAlex record spells it another. This
module holds the one normaliser every external source uses, so matching
behaves identically everywhere and improving it improves all of them.

Approach: strip punctuation, legal suffixes and country tags, collapse
whitespace, lowercase. Deliberately conservative - it does not do fuzzy or
phonetic matching, because a wrong match here silently attributes another
company's money to yours, which is worse than a miss.
"""
from __future__ import annotations

import re
from typing import Optional

# Legal forms and corporate suffixes. Stripped from the END of a name only,
# repeatedly, so "Acme Therapeutics Holdings LLC" -> "acme therapeutics".
# "Therapeutics"/"Pharmaceuticals" are NOT stripped: they carry meaning and
# distinguish real companies from each other.
_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "llc", "lc", "llp", "lp", "plc", "pllc", "ltd", "limited",
    "gmbh", "ag", "sa", "nv", "bv", "ab", "oy", "as", "spa", "srl",
    "holdings", "holding", "group", "intl", "international",
    "usa", "us", "america", "american",
}

# Parenthetical country tags OpenAlex adds: "Pfizer (United States)"
_PAREN = re.compile(r"\s*\([^)]*\)\s*")
_NONALNUM = re.compile(r"[^a-z0-9]+")
_THE = re.compile(r"^the\s+")


def normalise_company_name(name: Optional[str]) -> str:
    """Return a canonical key for matching. Empty string if unusable."""
    if not name:
        return ""
    s = str(name).lower().strip()
    s = _PAREN.sub(" ", s)          # drop "(United States)" etc.
    s = s.replace("&", " and ")
    s = _NONALNUM.sub(" ", s)       # punctuation -> space
    s = _THE.sub("", s)
    parts = s.split()

    # strip trailing legal suffixes repeatedly
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()

    return " ".join(parts).strip()


def is_too_generic(key: str) -> bool:
    """True when a name is short or common enough that an exact match could
    plausibly be a different company.

    Only meaningful when there is no other corroboration - if two records
    agree on the US state, that is far stronger evidence than name length,
    so callers should check the state first and consult this only when the
    state is unknown. (An early version rejected "statadx" for being seven
    characters, despite an exact state match. Distinctiveness is not length.)
    """
    if not key:
        return True
    if len(key) < 4:
        return True
    # a single very short token, e.g. "acme", "apex"
    if len(key.split()) == 1 and len(key) < 6:
        return True
    return False


# ----------------------------------------------------------------------
# US state extraction, used as a secondary check on name matches
# ----------------------------------------------------------------------

_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR",
}
_STATE_RE = re.compile(
    r"\b(" + "|".join(sorted(_STATE_ABBR, key=len, reverse=True)) + r")\b",
    flags=re.I)

# Words that mean the preceding token was a STREET, not a state:
#   "201 Washington St., Boston, Massachusetts"  -> MA, not WA
_STREET_WORD = re.compile(
    r"^\W*(st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|way|ln|lane"
    r"|pl|place|ct|court|pkwy|parkway|hwy|highway|sq|square|ter|terrace"
    r"|cir|circle|row|walk|park|plaza|bldg|building)\b",
    flags=re.I)


def state_from_address(address: Optional[str]) -> Optional[str]:
    """Pull a two-letter US state code out of a free-text address block.

    Two things make this harder than it looks, both found in real data:
      * street names collide with state names ("201 Washington St.")
      * the state sits near the END of the address, after the street
    So we take the LAST plausible match, skipping any followed by a street
    designator.
    """
    if not address:
        return None
    text = str(address)

    best = None
    for m in _STATE_RE.finditer(text):
        after = text[m.end():]
        if _STREET_WORD.match(after):
            continue                      # "Washington St." -> a street
        best = _STATE_ABBR[m.group(1).lower()]
    if best:
        return best

    # already-abbreviated form, e.g. "Waltham, MA 02451" - again take the last
    valid = set(_STATE_ABBR.values())
    for m in re.finditer(r",\s*([A-Z]{2})\b", text):
        if m.group(1) in valid:
            best = m.group(1)
    return best
