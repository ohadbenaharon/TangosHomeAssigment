"""Adverse-media screening.

The archive is large (~120k articles, ~50MB) and every entity is matched against
it, so it is scanned exactly **once** per run for the whole set of names, rather
than once per entity. Callers collect every name they will need first, then scan.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from orchestrator.matching import PhraseIndex
from orchestrator.models import Article, MediaFindings
from orchestrator.names import NameKey


class MediaIndex:
    """Result of one archive scan: which articles mention which known names."""

    def __init__(self, mentions: dict[NameKey, frozenset[str]]) -> None:
        self._mentions = mentions

    @classmethod
    def scan(cls, articles: Iterable[Article], names: PhraseIndex) -> MediaIndex:
        mentions: defaultdict[NameKey, set[str]] = defaultdict(set)
        for article in articles:
            # Title and body are matched separately so a name cannot be formed
            # across the seam ("... Aria Shipping" + "Lines ...").
            for key in names.find(article.title) | names.find(article.body):
                mentions[key].add(article.id)
        return cls({k: frozenset(v) for k, v in mentions.items()})

    def screen(self, keys: Iterable[NameKey]) -> MediaFindings:
        """Distinct articles mentioning any of the given names of one entity."""
        ids: set[str] = set()
        for key in keys:
            ids |= self._mentions.get(key, frozenset())
        return MediaFindings(article_ids=tuple(sorted(ids)))
