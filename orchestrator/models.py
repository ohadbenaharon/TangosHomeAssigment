"""Domain models. All immutable; value equality is what makes de-duplication work."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from orchestrator.names import NameKey, name_key


class Source(StrEnum):
    SANCTIONS = "sanctions"
    MEDIA = "media"
    REGISTRY = "registry"


@dataclass(frozen=True, slots=True)
class Investigation:
    """One row of the investigations file."""

    name: str
    entity_type: str
    country: str
    key: NameKey = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", name_key(self.name))


@dataclass(frozen=True, slots=True, order=True)
class SanctionEntry:
    """One watchlist row. Identical rows compare equal, which is how
    duplicated watchlist entries collapse into one finding."""

    list_name: str
    risk: str
    country: str
    name: str
    aliases: tuple[str, ...] = ()

    @property
    def keys(self) -> frozenset[NameKey]:
        """Every name this entry is known by, normalised."""
        return frozenset(k for k in map(name_key, (self.name, *self.aliases)) if k)


@dataclass(frozen=True, slots=True)
class Article:
    id: str
    published: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class MediaFindings:
    article_ids: tuple[str, ...]  # distinct, sorted

    @property
    def count(self) -> int:
        return len(self.article_ids)


@dataclass(frozen=True, slots=True)
class Officer:
    """A person registered against a company. One person holding several
    positions is one officer with several roles."""

    name: str
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LinkedCompany:
    name: str
    relations: tuple[str, ...]  # subsidiary / shareholder / parent


@dataclass(frozen=True, slots=True)
class RegistryRecord:
    id: str
    name: str
    country: str
    officers: tuple[Officer, ...]
    linked: tuple[LinkedCompany, ...]


@dataclass(frozen=True, slots=True)
class RegistryFindings:
    """Result of a registry lookup that completed; ``record`` is None if the
    entity has no registry record."""

    record: RegistryRecord | None


# --- Per-source outcome --------------------------------------------------------
# A compliance report must distinguish "checked, nothing found" from "not checked"
# from "check failed". An empty list alone cannot express that.


@dataclass(frozen=True, slots=True)
class Ran[T]:
    value: T


@dataclass(frozen=True, slots=True)
class NotRun:
    pass


@dataclass(frozen=True, slots=True)
class Failed:
    error: str


type Outcome[T] = Ran[T] | NotRun | Failed

NOT_RUN = NotRun()


@dataclass(frozen=True, slots=True)
class Screening:
    """Watchlist + media results for one name. Computed once per distinct name per
    run and shared by every place that name appears (primary or follow-up)."""

    sanctions: Outcome[tuple[SanctionEntry, ...]]
    media: Outcome[MediaFindings]

    @property
    def has_findings(self) -> bool:
        match self.sanctions:
            case Ran(value=entries) if entries:
                return True
            case Failed():
                return True
        match self.media:
            case Ran(value=media) if media.count:
                return True
            case Failed():
                return True
        return False


class FollowUpKind(StrEnum):
    OFFICER = "officer"
    LINKED = "linked"


@dataclass(frozen=True, slots=True)
class FollowUp:
    """An officer or linked company discovered in a registry record, screened
    against the watchlist and media (never the registry: depth is limited to 1)."""

    name: str
    kind: FollowUpKind
    relationship: tuple[str, ...]  # officer roles, or link relations
    screening: Screening


@dataclass(frozen=True, slots=True)
class EntityReport:
    entity: str
    entity_type: str
    country: str
    sanctions: Outcome[tuple[SanctionEntry, ...]]
    media: Outcome[MediaFindings]
    registry: Outcome[RegistryFindings]
    # Mirrors the registry outcome: not run / failed with it; Ran(()) if no record.
    follow_ups: Outcome[tuple[FollowUp, ...]] = NOT_RUN

    @property
    def outcomes(self) -> dict[Source, Outcome[object]]:
        return {
            Source.SANCTIONS: self.sanctions,
            Source.MEDIA: self.media,
            Source.REGISTRY: self.registry,
        }
