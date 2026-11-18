"""The single definition of "the same name" used across the whole orchestrator.

Every comparison between names (investigations vs. watchlist, registry cache keys,
follow-up de-duplication, media mentions) goes through :func:`name_key`, so the
matching rule is defined once and cannot drift between data sources.

A name is reduced to its sequence of word tokens: Unicode-normalised (NFKC),
case-folded, split on anything that is not a word character. Two names are the
same entity name iff their token sequences are equal. Matching on token
sequences gives "whole phrase on word boundaries" semantics for free: a phrase
can only match a run of complete tokens, never part of a word.
"""

from __future__ import annotations

import re
import unicodedata

type NameKey = tuple[str, ...]
"""A normalised entity name: its case-folded word tokens, in order."""

_WORD = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    """Split free text into normalised word tokens."""
    return _WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def name_key(name: str) -> NameKey:
    """Normalise an entity name into the key used for all name comparisons."""
    return tuple(tokenize(name))
