"""Runs a batch of investigations across the data sources and assembles reports.

Run shape. The phases are explicit because the media scan must know *every*
name before it starts, and follow-up names are only known after the registry:

0. Routing: drop investigations the rules exclude; pick the enabled sources.
1. De-duplicate investigations by normalised name (first occurrence wins).
2. Registry fan-out: look every entity up concurrently (I/O bound, ~300ms each).
3. Collect follow-ups (officers, linked companies) from the records found.
4. Screen every distinct name -- primary or follow-up -- exactly once:
   watchlist lookup, plus one media-archive pass for all names together.
5. Assemble one report per entity, in input order.

"Do not investigate the same entity twice" is enforced by keying all screening
on the normalised name: a follow-up that is also a primary investigation, or an
officer sitting on several boards, is screened once and the result shared.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

from orchestrator.matching import PhraseIndex
from orchestrator.models import (
    NOT_RUN,
    Article,
    EntityReport,
    Failed,
    FollowUp,
    FollowUpKind,
    Investigation,
    MediaFindings,
    NotRun,
    Outcome,
    Ran,
    RegistryFindings,
    RegistryRecord,
    SanctionEntry,
    Screening,
    Source,
)
from orchestrator.names import NameKey, name_key
from orchestrator.routing import RoutingRules
from orchestrator.sources.media import MediaIndex
from orchestrator.sources.registry import CachingRegistry
from orchestrator.sources.sanctions import Watchlist

log = logging.getLogger(__name__)

type ArticleStream = Callable[[], Iterable[Article]]

DEFAULT_REGISTRY_WORKERS = 16


@dataclass(frozen=True, slots=True)
class Orchestrator:
    watchlist: Watchlist
    articles: ArticleStream
    registry: CachingRegistry | None = None
    rules: RoutingRules = field(default_factory=RoutingRules)
    registry_workers: int = DEFAULT_REGISTRY_WORKERS

    def __post_init__(self) -> None:
        if Source.REGISTRY in self.sources and self.registry is None:
            raise ValueError("registry source enabled but no registry client given")
        if self.registry_workers < 1:
            raise ValueError("registry_workers must be at least 1")

    @property
    def sources(self) -> frozenset[Source]:
        return self.rules.enabled_sources()

    def run(self, investigations: Iterable[Investigation]) -> list[EntityReport]:
        # Routing before de-duplication: rules judge each input row on its own.
        entities = dedupe(i for i in investigations if self.rules.admits(i))
        with _timed("registry lookups (%d entities)", len(entities)):
            registry = self._lookup_all(entities)

        # Every distinct name to screen: primaries first, then follow-ups.
        names: dict[NameKey, str] = {e.key: e.name for e in entities}
        for outcome in registry.values():
            for follow_up_name, _, _ in _follow_up_targets(outcome):
                names.setdefault(name_key(follow_up_name), follow_up_name)
        screenings = self._screen_all(names.keys())
        log.info(
            "screened %d distinct names (%d primary, %d follow-up only)",
            len(names),
            len(entities),
            len(names) - len(entities),
        )
        return [self._report(e, registry[e.key], screenings) for e in entities]

    # --- phase 2: registry ---------------------------------------------------------

    def _lookup_all(
        self, entities: Sequence[Investigation]
    ) -> dict[NameKey, Outcome[RegistryFindings]]:
        registry = self.registry
        if registry is None or Source.REGISTRY not in self.sources:
            return {e.key: NOT_RUN for e in entities}
        # The cache makes repeated names free and shares in-flight requests, so
        # this is safe even if the same name were submitted twice.
        with ThreadPoolExecutor(self.registry_workers, thread_name_prefix="registry") as pool:
            futures = {e.key: pool.submit(registry.lookup, e.name) for e in entities}
            return {key: future.result() for key, future in futures.items()}

    # --- phase 4: screening ---------------------------------------------------------

    def _screen_all(self, keys: Iterable[NameKey]) -> dict[NameKey, Screening]:
        identities = {key: self.watchlist.aliases_of(key) for key in keys}
        media = None
        if Source.MEDIA in self.sources:
            with _timed("media scan (%d names)", len(identities)):
                media = self._scan_media(identities.values())
        return {key: self._screen(key, ids, media) for key, ids in identities.items()}

    def _scan_media(self, identities: Iterable[frozenset[NameKey]]) -> MediaIndex:
        # Watchlist names join the index even when not investigated: the more
        # names it knows, the better it can tell a nested mention apart.
        names = PhraseIndex(self.watchlist.names.union(*identities))
        return MediaIndex.scan(self.articles(), names)

    def _screen(
        self, key: NameKey, identity: frozenset[NameKey], media: MediaIndex | None
    ) -> Screening:
        sanctions: Outcome[tuple[SanctionEntry, ...]] = (
            Ran(self.watchlist.screen(key)) if Source.SANCTIONS in self.sources else NOT_RUN
        )
        media_outcome: Outcome[MediaFindings] = (
            Ran(media.screen(identity)) if media is not None else NOT_RUN
        )
        return Screening(sanctions=sanctions, media=media_outcome)

    # --- phase 5: assembly ------------------------------------------------------------

    def _report(
        self,
        entity: Investigation,
        registry: Outcome[RegistryFindings],
        screenings: dict[NameKey, Screening],
    ) -> EntityReport:
        own = screenings[entity.key]
        return EntityReport(
            entity=entity.name,
            entity_type=entity.entity_type,
            country=entity.country,
            sanctions=own.sanctions,
            media=own.media,
            registry=registry,
            follow_ups=_follow_ups(registry, screenings),
        )


def _follow_ups(
    registry: Outcome[RegistryFindings], screenings: dict[NameKey, Screening]
) -> Outcome[tuple[FollowUp, ...]]:
    match registry:
        case NotRun():
            return NOT_RUN
        case Failed():
            return Failed("registry lookup failed; follow-ups unknown")
    return Ran(
        tuple(
            FollowUp(name, kind, relationship, screenings[name_key(name)])
            for name, kind, relationship in _follow_up_targets(registry)
        )
    )


def _follow_up_targets(
    registry: Outcome[RegistryFindings],
) -> Iterator[tuple[str, FollowUpKind, tuple[str, ...]]]:
    """Officers then linked companies of a found record, excluding the record itself."""
    if not isinstance(registry, Ran) or registry.value.record is None:
        return
    record: RegistryRecord = registry.value.record
    own = name_key(record.name)
    for officer in record.officers:
        if name_key(officer.name) != own:
            yield officer.name, FollowUpKind.OFFICER, officer.roles
    for company in record.linked:
        if name_key(company.name) != own:
            yield company.name, FollowUpKind.LINKED, company.relations


def dedupe(investigations: Iterable[Investigation]) -> Sequence[Investigation]:
    """Distinct entities by normalised name, keeping the first occurrence and input order."""
    seen: dict[NameKey, Investigation] = {}
    for inv in investigations:
        seen.setdefault(inv.key, inv)
    return list(seen.values())


@contextmanager
def _timed(what: str, *args: object) -> Iterator[None]:
    started = time.perf_counter()
    yield
    log.info(what + " took %.2fs", *args, time.perf_counter() - started)
