"""Runs a batch of investigations across the data sources and assembles reports.

Run shape (phases are explicit because the media scan needs *every* name up front):

1. De-duplicate investigations by normalised name (first occurrence wins).
2. Resolve each entity's identity: its own name plus the names/aliases the
   watchlist knows it by.
3. Scan the media archive once for all identities.
4. Assemble one report per distinct entity, in input order.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from orchestrator.matching import PhraseIndex
from orchestrator.models import (
    NOT_RUN,
    Article,
    EntityReport,
    Investigation,
    MediaFindings,
    Outcome,
    Ran,
    SanctionEntry,
    Source,
)
from orchestrator.names import NameKey
from orchestrator.sources.media import MediaIndex
from orchestrator.sources.sanctions import Watchlist

type ArticleStream = Callable[[], Iterable[Article]]


@dataclass(frozen=True, slots=True)
class Orchestrator:
    watchlist: Watchlist
    articles: ArticleStream
    sources: frozenset[Source] = frozenset(Source)

    def run(self, investigations: Iterable[Investigation]) -> list[EntityReport]:
        entities = dedupe(investigations)
        identities = {e.key: self.watchlist.aliases_of(e.key) for e in entities}
        media = self._scan_media(identities.values()) if Source.MEDIA in self.sources else None
        return [self._report(e, identities[e.key], media) for e in entities]

    def _scan_media(self, identities: Iterable[frozenset[NameKey]]) -> MediaIndex:
        # Watchlist names join the index even when not investigated: the more
        # names it knows, the better it can tell a nested mention apart.
        names = PhraseIndex(self.watchlist.names.union(*identities))
        return MediaIndex.scan(self.articles(), names)

    def _report(
        self, entity: Investigation, identity: frozenset[NameKey], media: MediaIndex | None
    ) -> EntityReport:
        sanctions: Outcome[tuple[SanctionEntry, ...]] = (
            Ran(self.watchlist.screen(entity.key)) if Source.SANCTIONS in self.sources else NOT_RUN
        )
        media_outcome: Outcome[MediaFindings] = (
            Ran(media.screen(identity)) if media is not None else NOT_RUN
        )
        return EntityReport(
            entity=entity.name,
            entity_type=entity.entity_type,
            country=entity.country,
            sanctions=sanctions,
            media=media_outcome,
        )


def dedupe(investigations: Iterable[Investigation]) -> Sequence[Investigation]:
    """Distinct entities by normalised name, keeping the first occurrence and input order."""
    seen: dict[NameKey, Investigation] = {}
    for inv in investigations:
        seen.setdefault(inv.key, inv)
    return list(seen.values())
