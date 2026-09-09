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


# ----------------------------------------------------------------------
# Corroborating a name match
#
# An exact normalised-name match is never proof on its own: company names
# are not unique, so "Peptech Corp." in our list and "Peptech Corp." in an
# external file could be two unrelated businesses. Something independent
# has to agree before the match is trusted.
#
# The evidence is asymmetric, and getting that wrong cost us real matches:
#   agreement    is strong    - two records independently placing the same
#                               name in the same state is a real confirmation
#   disagreement is WEAK      - companies relocate, and sources record the
#                               address as at the time of the record. SBIR
#                               stamps the address of a 1998 award and never
#                               revisits it.
#
# Measured on 380 SBIR name matches: 37 disagreed on state, and 12 of those
# had an IDENTICAL website domain on both sides - the same company after a
# move (BrightSpec MA/VA both brightspec.com; Aprea MA/PA both aprea.com).
# So a state disagreement must not veto a match. Only a website that
# genuinely differs is evidence against.
# ----------------------------------------------------------------------

_VALID_CODES = set(_STATE_ABBR.values())


def state_code(value: Optional[str]) -> Optional[str]:
    """Normalise one state value to its two-letter code.

    Sources disagree on spelling: SEC Form D stores "MA", SBIR stores
    "Massachusetts". Both must become "MA" before they can be compared.
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) == 2 and s.upper() in _VALID_CODES:
        return s.upper()
    return _STATE_ABBR.get(s.lower())


_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_LEADING_WWW = re.compile(r"^www\d?\.", re.I)

#: Domains that appear in a "company website" field but identify no company.
_NON_COMPANY_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "crunchbase.com", "bloomberg.com", "wikipedia.org",
    "gmail.com", "yahoo.com", "hotmail.com", "aol.com", "outlook.com",
    "sbir.gov", "sam.gov", "google.com", "sites.google.com",
}


def website_domain(url: Optional[str]) -> Optional[str]:
    """Reduce a URL to a bare comparable domain, or None if unusable.

        "https://www.Acme.com/about?x=1"  ->  "acme.com"
        "acme.com"                        ->  "acme.com"
        "-"                               ->  None

    A domain outlives an address: a company that moves states keeps its
    website, which is exactly what makes this the tie-breaker when the
    states disagree.
    """
    if url is None:
        return None
    s = str(url).strip().lower()
    if s in ("", "-", "nan", "none", "n/a", "na"):
        return None
    s = _SCHEME.sub("", s)
    s = s.split("/")[0].split("?")[0].split("#")[0]
    s = s.split("@")[-1].split(":")[0]        # drop any user@ or :port
    s = _LEADING_WWW.sub("", s).strip(".")
    # a bare word is not a domain
    if "." not in s or len(s) < 4:
        return None
    # Some records put a social or directory profile in the website field
    # (SBIR lists linkedin.com as Xsphera's site). That identifies nobody,
    # and treating it as a company domain would make every such company
    # look like it shares a website with every other.
    if s in _NON_COMPANY_DOMAINS:
        return None
    return s or None


_TLDS = ("co.uk", "com.au", "co.in", "com", "net", "org", "io", "ai", "bio",
         "us", "co", "inc", "life", "health", "tech", "science")

#: Below this, a shared domain stem proves nothing - "bio.com" and
#: "biotech.com" are not evidence of the same company.
_MIN_STEM = 5


def _domain_stem(domain: str) -> str:
    """The distinctive label of a domain, without subdomain, TLD or hyphens.

        "nflectionrx.com"        ->  "nflectionrx"
        "international.neb.com"  ->  "neb"        (subdomain dropped)
        "e-trolz.com"            ->  "etrolz"     (hyphen dropped)

    Hyphens go because one source writes "e-trolz.com" and the other
    "etrolz.com" for the same company - a punctuation difference, exactly
    like the ones normalise_company_name() already removes from names.
    """
    s = domain.lower().strip(".")
    for tld in _TLDS:
        if s.endswith("." + tld):
            s = s[: -(len(tld) + 1)]
            break
    return s.rsplit(".", 1)[-1].replace("-", "")


def domains_related(a: Optional[str], b: Optional[str]) -> bool:
    """True when two domains plausibly belong to the same company.

    Companies routinely run more than one domain, and a plain string
    comparison calls those a mismatch. Two real cases from the SBIR match:

        nflection.com  /  nflectionrx.com   SAME - the second redirects to
                                            the first; "rx" is a suffix on
                                            the same stem
        casimirtrials.com / casimirspace.com  DIFFERENT - a clinical trials
                                            company and a space company
                                            that happen to share "casimir"

    So the test is containment, not a shared prefix: one stem must contain
    the whole of the other. "nflection" is inside "nflectionrx", whereas
    neither "casimirtrials" nor "casimirspace" contains the other.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    sa, sb = _domain_stem(a), _domain_stem(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True                       # same stem, different TLD
    short, long = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    return len(short) >= _MIN_STEM and short in long


#: Confidence levels whose data is trustworthy enough to reach a cache.
#: "suspect" is deliberately excluded - it is kept in the review sheet so a
#: human can look, but its money is never attributed to a company.
TRUSTED_CONFIDENCE = ("high", "unverified")


def match_confidence(key: str,
                     our_state: Optional[str] = None,
                     their_state: Optional[str] = None,
                     our_domain: Optional[str] = None,
                     their_domains=None) -> str:
    """Grade a name match using whatever corroboration is available.

    `their_states`/`their_domains` may hold several values, because an
    external source can carry decades of records for one company under
    several addresses and websites. Agreement with ANY of them counts.

    The website is the deciding field. Names are written a dozen ways and
    addresses go stale, but a domain is a registered identifier a company
    keeps through a move and a rename. So:

      website agrees     -> the same company, whatever the address says
      website disagrees  -> different companies, whatever the address says
      no website to check -> the name match stands on its own

    Returns:
      high        the name matched and nothing contradicts it
      suspect     the websites actively disagree - two different companies
                  sharing a name; or a name too generic to mean anything
                  with no state or website to corroborate it
      unverified  retained for callers that still expect it; not produced

    Note what is NOT here: a differing STATE never downgrades a match.
    Companies relocate and sources freeze the address as at the time of the
    record - SBIR stamps a 1998 award with a 1998 address and never
    revisits it. Measured on this datasource, treating a state difference
    as evidence against would have discarded 12 companies that a matching
    website proved were the same, plus 23 more that had no website either
    way and so could not defend themselves.
    """
    their_domains = {d for d in (their_domains or []) if d}
    # Related, not identical: a company may run nflection.com and
    # nflectionrx.com. See domains_related().
    domain_agrees = bool(our_domain and any(domains_related(our_domain, d)
                                            for d in their_domains))
    domain_differs = bool(our_domain and their_domains and not domain_agrees)

    their_states = their_state if isinstance(their_state, (set, list, tuple)) \
        else ([their_state] if their_state else [])
    their_states = {s for s in their_states if s}
    state_agrees = bool(our_state and our_state in their_states)

    # Anything that AGREES settles it, and agreement is checked first.
    # A company can rebrand and change domain while staying put - Lyra
    # Therapeutics was 480 Biomedical, Anagram was Synspira, Biomedical
    # Research Models trades as biomere.com - and all five such cases in
    # this datasource still sit in the state their old awards name. So a
    # differing website must not override a state that agrees.
    if domain_agrees or state_agrees:
        return "high"

    # Nothing agrees. A website that actively disagrees is then the one
    # piece of evidence strong enough to reject on.
    if domain_differs:
        return "suspect"

    # Nothing to compare at all. The name match stands on its own unless
    # the name is common enough that two companies could plausibly share
    # it - "imt", "emd" - in which case there is genuinely nothing here.
    if is_too_generic(key):
        return "suspect"
    return "high"
