"""Routing rules: which investigations run, and which sources run for them.

Every rule dimension (source, country, entity name) has the same semantics,
implemented once in :class:`IncludeExclude`:

- several include rules of one dimension are OR'd (match any of them);
- several exclude rules of one dimension are OR'd;
- exclude beats include;
- no include rule for a dimension means everything is included for it.

Dimensions are AND'd: an investigation runs only if every dimension admits it.

Country and name rules select *primary* investigations (rows of the
investigations file). Source rules apply to every check that runs.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from orchestrator.models import Investigation, Source


@dataclass(frozen=True, slots=True)
class IncludeExclude[T]:
    include: frozenset[T] = frozenset()
    exclude: frozenset[T] = frozenset()

    def admits(self, matches: Callable[[T], bool]) -> bool:
        """Whether a value is admitted, given how to test it against one rule item."""
        if any(matches(rule) for rule in self.exclude):
            return False
        return not self.include or any(matches(rule) for rule in self.include)


@dataclass(frozen=True, slots=True)
class RoutingRules:
    sources: IncludeExclude[Source] = field(default_factory=IncludeExclude)
    countries: IncludeExclude[str] = field(default_factory=IncludeExclude)
    # Patterns are applied with re.search to the entity name as written in the
    # input: unanchored and case-sensitive unless the pattern says otherwise.
    names: IncludeExclude[re.Pattern[str]] = field(default_factory=IncludeExclude)

    def enabled_sources(self) -> frozenset[Source]:
        return frozenset(s for s in Source if self.sources.admits(partial(operator.eq, s)))

    def admits(self, investigation: Investigation) -> bool:
        country = investigation.country.upper()
        return self.countries.admits(lambda rule: rule.upper() == country) and self.names.admits(
            lambda pattern: pattern.search(investigation.name) is not None
        )
