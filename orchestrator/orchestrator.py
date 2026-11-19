"""Runs a batch of investigations across the data sources and assembles reports.

Run shape (phases are explicit because the media scan needs *every* name up front):

0. Apply routing rules: drop investigations the rules exclude, pick sources.
1. De-duplicate investigations by normalised name (first occurrence wins).
2. Resolve each entity's identity: its own name plus the names/aliases the
   watchlist knows it by.
3. Look each entity up in the registry (cached).
4. Scan the media archive once for all identities.
5. Assemble one report per distinct entity, in input order.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from orchestrator.matching import PhraseIndex
from orchestrator.models import (
    NOT_RUN,
    Article,
    EntityReport,
    Investigation,
    MediaFindings,
    Outcome,
    Ran,
    RegistryFindings,
    SanctionEntry,
    Source,
)
from orchestrator.names import NameKey
from orchestrator.routing import RoutingRules
from orchestrator.sources.media import MediaIndex
from orchestrator.sources.registry import CachingRegistry
from orchestrator.sources.sanctions import Watchlist

type ArticleStream = Callable[[], Iterable[Article]]


@dataclass(frozen=True, slots=True)
class Orchestrator:
    watchlist: Watchlist
    articles: ArticleStream
    registry: CachingRegistry | None = None
    rules: RoutingRules = field(default_factory=RoutingRules)

    def __post_init__(self) -> None:
        if Source.REGISTRY in self.sources and self.registry is None:
            raise ValueError("registry source enabled but no registry client given")

    @property
    def sources(self) -> frozenset[Source]:
        return self.rules.enabled_sources()

    def run(self, investigations: Iterable[Investigation]) -> list[EntityReport]:
        # Routing before de-duplication: rules judge each input row on its own.
        entities = dedupe(i for i in investigations if self.rules.admits(i))
        identities = {e.key: self.watchlist.aliases_of(e.key) for e in entities}
        registry = {e.key: self._registry(e) for e in entities}
        media = self._scan_media(identities.values()) if Source.MEDIA in self.sources else None
        return [self._report(e, identities[e.key], registry[e.key], media) for e in entities]

    def _registry(self, entity: Investigation) -> Outcome[RegistryFindings]:
        if self.registry is None or Source.REGISTRY not in self.sources:
            return NOT_RUN
        return self.registry.lookup(entity.name)

    def _scan_media(self, identities: Iterable[frozenset[NameKey]]) -> MediaIndex:
        # Watchlist names join the index even when not investigated: the more
        # names it knows, the better it can tell a nested mention apart.
        names = PhraseIndex(self.watchlist.names.union(*identities))
        return MediaIndex.scan(self.articles(), names)

    def _report(
        self,
        entity: Investigation,
        identity: frozenset[NameKey],
        registry: Outcome[RegistryFindings],
        media: MediaIndex | None,
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
            registry=registry,
        )


def dedupe(investigations: Iterable[Investigation]) -> Sequence[Investigation]:
    """Distinct entities by normalised name, keeping the first occurrence and input order."""
    seen: dict[NameKey, Investigation] = {}
    for inv in investigations:
        seen.setdefault(inv.key, inv)
    return list(seen.values())
