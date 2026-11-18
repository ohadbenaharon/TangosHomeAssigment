"""Sanctions watchlist screening.

An entity matches a watchlist row when the entity's name equals the row's name
or one of its aliases (after normalisation, see :mod:`orchestrator.names`).

Equality rather than substring: a watchlist field *is* a whole name, so "appears
as a whole phrase" means the whole field. Substring matching would flag
"Aria Shipping" against a listing for "Aria Shipping Lines" -- exactly the false
positive the README rules out for media.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from orchestrator.models import SanctionEntry
from orchestrator.names import NameKey


class Watchlist:
    def __init__(self, entries: Iterable[SanctionEntry]) -> None:
        index: defaultdict[NameKey, set[SanctionEntry]] = defaultdict(set)
        for entry in entries:
            for key in entry.keys:
                index[key].add(entry)  # a set: identical rows become one finding
        self._index: dict[NameKey, frozenset[SanctionEntry]] = {
            k: frozenset(v) for k, v in index.items()
        }

    @property
    def names(self) -> frozenset[NameKey]:
        """Every name and alias on the list."""
        return frozenset(self._index)

    def screen(self, key: NameKey) -> tuple[SanctionEntry, ...]:
        """Distinct watchlist entries matching a name, in a stable order."""
        return tuple(sorted(self._index.get(key, ())))

    def aliases_of(self, key: NameKey) -> frozenset[NameKey]:
        """All names the watchlist knows this entity by, including ``key`` itself.

        One hop only: names of the rows that match ``key``. We do not chase
        aliases-of-aliases across rows, which could chain unrelated entities.
        """
        return frozenset({key}.union(*(e.keys for e in self._index.get(key, ()))))
