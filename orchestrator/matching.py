"""Finding mentions of known entity names in free text.

Matching rule (README): a name matches when it appears as a whole phrase on word
boundaries, case-insensitively -- and ``Aria Shipping`` does **not** match text
that only mentions ``Aria Shipping Lines``.

A plain ``\\bAria Shipping\\b`` regex *does* match inside "Aria Shipping Lines"
(the space after "Shipping" is a word boundary), so word boundaries alone cannot
implement the rule. The rule we implement instead is:

    A mention of name A is discarded when it lies strictly inside a mention
    of another known name B.

"Known names" is the universe the index is built with, so the more names it is
given (investigations, watchlist names and aliases, registry follow-ups), the
better it resolves nested names. A longer name it has never heard of cannot
shadow a shorter one -- that limitation is inherent to dictionary matching and
is documented in NOTES.md.

Implementation: text is tokenised once (see :mod:`orchestrator.names`) and each
token position is checked against the names that start with that token, so the
cost is linear in the text length rather than (names x text).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from orchestrator.names import NameKey, tokenize


class PhraseIndex:
    """An immutable dictionary of names that can locate their mentions in text."""

    def __init__(self, names: Iterable[NameKey]) -> None:
        by_first: defaultdict[str, set[int]] = defaultdict(set)
        known: set[NameKey] = set()
        for key in names:
            if not key:  # a name with no word characters can never be mentioned
                continue
            known.add(key)
            by_first[key[0]].add(len(key))
        self._names: frozenset[NameKey] = frozenset(known)
        # first token -> candidate name lengths, longest first
        self._lengths_by_first: dict[str, tuple[int, ...]] = {
            first: tuple(sorted(lengths, reverse=True)) for first, lengths in by_first.items()
        }

    def __len__(self) -> int:
        return len(self._names)

    def __contains__(self, key: object) -> bool:
        return key in self._names

    def find(self, text: str) -> set[NameKey]:
        """Return every known name mentioned in ``text`` (nested mentions excluded)."""
        return self.find_in_tokens(tokenize(text))

    def find_in_tokens(self, tokens: list[str]) -> set[NameKey]:
        spans: list[tuple[int, int, NameKey]] = []
        lengths_by_first = self._lengths_by_first
        names = self._names
        for start, token in enumerate(tokens):
            lengths = lengths_by_first.get(token)
            if lengths is None:
                continue
            for length in lengths:
                candidate = tuple(tokens[start : start + length])
                if len(candidate) == length and candidate in names:
                    spans.append((start, start + length, candidate))
        return {key for _, _, key in _drop_nested(spans)}


def _drop_nested(spans: list[tuple[int, int, NameKey]]) -> list[tuple[int, int, NameKey]]:
    """Remove spans that lie strictly inside another span.

    Sorted by (start asc, end desc), a span is nested iff some earlier span
    already reaches at least as far. Spans are unique (a span determines its
    tokens), so "at least as far" can only mean "strictly contains".
    Overlapping-but-not-nested spans are both kept.
    """
    spans.sort(key=lambda span: (span[0], -span[1]))
    kept: list[tuple[int, int, NameKey]] = []
    furthest_end = -1
    for span in spans:
        if span[1] > furthest_end:
            kept.append(span)
            furthest_end = span[1]
    return kept
