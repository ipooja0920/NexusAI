"""Corporate groups: a parent and its subsidiaries listed once.

Capital IQ lists many subsidiaries as companies of their own ("Lantheus
Medical Imaging, Inc." and its parent "Lantheus Holdings, Inc."; three
Sanofi US entities). In the output each group is listed once - by its
best-placed member - and the others are marked "Same group as ...".

Two companies are in one group when
  * one is the parent named in the other's description ("operates as a
    subsidiary of X"), or both name the same parent (names compared without
    ticker, punctuation and Inc / LLC / Corp - "Merck & Co." is not "Merck
    KGaA"), or
  * their names are the same once the ticker, legal form and words such as
    Holdings / Group / USA are dropped ("Integra LifeSciences Corporation"
    and "Integra LifeSciences Holdings Corporation") - for names of two or
    more words; a one-word name joins a group only through a parent link.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

_BRACKET = re.compile(r"\([^)]*\)")
_DROP = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co",
    "company", "plc", "lp", "llp", "sa", "ag", "nv", "bv", "gmbh", "kgaa", "kk",
    "spa", "srl", "pty", "holdings", "holding", "group", "the", "usa", "us",
    "americas", "america", "international", "and",
}


def group_key(name) -> str:
    """'lantheus' from 'Lantheus Holdings, Inc. (NasdaqGM:LNTH)'."""
    s = _BRACKET.sub(" ", "" if name is None else str(name)).lower()
    s = s.replace("u.s.a.", " ").replace("u.s.", " ")
    words = re.findall(r"[a-z0-9]+", s)
    out = []
    for w in words:
        if w in _DROP or len(w) == 1:
            continue
        if len(w) > 3 and w.endswith("s"):
            w = w[:-1]
        out.append(w)
    return " ".join(out)


_LEGAL = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co",
          "company", "the", "lp", "llp"}


def parent_key(name) -> str:
    """Stricter than group_key, for matching a parent's name: only the
    ticker, punctuation and the commonest legal forms go, so "Merck & Co."
    and "Merck KGaA" (two different companies) stay apart."""
    s = _BRACKET.sub(" ", "" if name is None else str(name)).lower()
    return " ".join(w for w in re.findall(r"[a-z0-9]+", s) if w not in _LEGAL)


def build_groups(companies: List[dict]) -> List[Optional[int]]:
    """Group id per company (the index of one member), or None for a
    company that is in no group with any other."""
    n = len(companies)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_key: Dict[str, int] = {}
    keys = [group_key(c.get("company")) for c in companies]
    named: Dict[str, List[int]] = {}     # name -> every company with it
    for i, c in enumerate(companies):
        k = parent_key(c.get("company"))
        if k:
            named.setdefault(k, []).append(i)
    for i, k in enumerate(keys):
        # one-word names ("Endodontics", "Allentown") are too common to tie
        # two companies together on the name alone
        if len(k.split()) < 2:
            continue
        if k in by_key:
            union(i, by_key[k])
        else:
            by_key[k] = i
    by_parent: Dict[str, int] = {}
    for i, c in enumerate(companies):
        pk = parent_key(c.get("parent_company"))
        if not pk:
            continue
        for j in named.get(pk, ()):
            union(i, j)
        if pk in by_parent:
            union(i, by_parent[pk])
        else:
            by_parent[pk] = i
    roots = [find(i) for i in range(n)]
    size: Dict[int, int] = {}
    for r in roots:
        size[r] = size.get(r, 0) + 1
    return [r if size[r] > 1 else None for r in roots]


def pick_listed(order: Iterable[int], groups: List[Optional[int]], n: int,
                blocked: Set[int] = frozenset()) -> Tuple[List[int], Dict[int, int]]:
    """The first `n` of `order` with at most one member per group, skipping
    groups in `blocked`. -> (listed, {left-out member: member it gave way
    to, or -1 when its group is listed in another tab})."""
    listed, seen, gave_way = [], {}, {}
    for gi in order:
        g = groups[gi] if gi < len(groups) else None
        if g is not None and g in blocked:
            gave_way[gi] = -1
            continue
        if g is not None and g in seen:
            gave_way[gi] = seen[g]
            continue
        if len(listed) >= n:
            continue
        listed.append(gi)
        if g is not None:
            seen[g] = gi
    return listed, gave_way
