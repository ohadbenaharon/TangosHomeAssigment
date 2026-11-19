from __future__ import annotations

import json
import re
import threading
import time

from orchestrator.models import (
    NOT_RUN,
    Article,
    EntityReport,
    Failed,
    FollowUp,
    FollowUpKind,
    Investigation,
    LinkedCompany,
    Officer,
    Ran,
    RegistryRecord,
    SanctionEntry,
    Source,
)
from orchestrator.names import NameKey
from orchestrator.orchestrator import Orchestrator
from orchestrator.report import render_json, render_text
from orchestrator.routing import IncludeExclude, RoutingRules
from orchestrator.sources.registry import CachingRegistry
from orchestrator.sources.sanctions import Watchlist
from tests.conftest import article, sanction
from tests.test_orchestrator import StubRegistry

ARIA = RegistryRecord(
    id="REG-IR-0042",
    name="Aria Shipping Lines",
    country="IR",
    officers=(
        Officer("Farhad Noori", ("director",)),
        Officer("Dariush Rahimi", ("director",)),
    ),
    linked=(
        LinkedCompany("Caspian Trade Bank", ("subsidiary",)),
        LinkedCompany("Zagros Freight", ("shareholder",)),
        LinkedCompany("Aria Shipping Lines", ("parent",)),  # self-reference
    ),
)
ZAGROS = RegistryRecord(
    id="REG-IR-0050",
    name="Zagros Freight",
    country="IR",
    officers=(Officer("Dariush Rahimi", ("secretary",)),),  # shared with Aria
    linked=(),
)
WATCHLIST = Watchlist(
    [
        sanction("Aria Shipping Lines"),
        sanction("Dariush Rahimi", list_name="EU"),
        sanction("Caspian Trade Bank"),
        sanction("Caspian Trade Bank"),
    ]
)
ARTICLES = [
    article("AM-1", "Dariush Rahimi met officials."),
    article("AM-2", "Caspian Trade Bank and Dariush Rahimi."),
    article("AM-3", "Zagros Freight expands."),
]


class CountingWatchlist(Watchlist):
    def __init__(self, entries: list[SanctionEntry]) -> None:
        super().__init__(entries)
        self.screened: list[NameKey] = []

    def screen(self, key: NameKey) -> tuple[SanctionEntry, ...]:
        self.screened.append(key)
        return super().screen(key)


def inv(name: str, country: str = "IR") -> Investigation:
    return Investigation(name=name, entity_type="organization", country=country)


def run(
    *investigations: Investigation,
    rules: RoutingRules | None = None,
    stub: StubRegistry | None = None,
    watchlist: Watchlist = WATCHLIST,
) -> list[EntityReport]:
    registry = stub or StubRegistry({"Aria Shipping Lines": ARIA, "Zagros Freight": ZAGROS})
    return Orchestrator(
        watchlist,
        lambda: ARTICLES,
        registry=CachingRegistry(registry),
        rules=rules or RoutingRules(),
    ).run(investigations)


def follow_ups(report: EntityReport) -> dict[str, FollowUp]:
    assert isinstance(report.follow_ups, Ran)
    return {f.name: f for f in report.follow_ups.value}


def test_officers_and_linked_companies_are_screened_against_watchlist_and_media() -> None:
    [aria] = run(inv("Aria Shipping Lines"))
    found = follow_ups(aria)
    assert list(found) == ["Farhad Noori", "Dariush Rahimi", "Caspian Trade Bank", "Zagros Freight"]
    rahimi, bank = found["Dariush Rahimi"], found["Caspian Trade Bank"]
    assert rahimi.kind is FollowUpKind.OFFICER and rahimi.relationship == ("director",)
    assert isinstance(rahimi.screening.sanctions, Ran)
    assert [e.list_name for e in rahimi.screening.sanctions.value] == ["EU"]
    assert isinstance(rahimi.screening.media, Ran)
    assert rahimi.screening.media.value.article_ids == ("AM-1", "AM-2")
    assert bank.kind is FollowUpKind.LINKED
    assert isinstance(bank.screening.sanctions, Ran)
    assert len(bank.screening.sanctions.value) == 1  # duplicate watchlist rows collapse
    assert not found["Farhad Noori"].screening.has_findings


def test_a_record_does_not_follow_up_on_itself() -> None:
    [aria] = run(inv("Aria Shipping Lines"))
    assert "Aria Shipping Lines" not in follow_ups(aria)


def test_follow_ups_never_query_the_registry() -> None:
    stub = StubRegistry({"Aria Shipping Lines": ARIA})
    run(inv("Aria Shipping Lines"), stub=stub)
    assert stub.calls == ["Aria Shipping Lines"]


def test_each_distinct_name_is_screened_once_per_run() -> None:
    """Dariush Rahimi is an officer of two investigated companies; Zagros Freight
    is both a primary investigation and a follow-up of Aria."""
    counting = CountingWatchlist([sanction("Dariush Rahimi", list_name="EU")])
    aria, zagros = run(inv("Aria Shipping Lines"), inv("Zagros Freight"), watchlist=counting)
    assert sorted(counting.screened) == sorted(set(counting.screened))  # no repeats
    # The same screening result is shared, not recomputed.
    assert (
        follow_ups(aria)["Dariush Rahimi"].screening
        is follow_ups(zagros)["Dariush Rahimi"].screening
    )
    assert follow_ups(aria)["Zagros Freight"].screening.sanctions == zagros.sanctions


def test_media_archive_is_read_once_including_follow_up_names() -> None:
    reads = 0

    def articles() -> list[Article]:
        nonlocal reads
        reads += 1
        return ARTICLES

    stub = StubRegistry({"Aria Shipping Lines": ARIA})
    Orchestrator(WATCHLIST, articles, registry=CachingRegistry(stub)).run(
        [inv("Aria Shipping Lines")]
    )
    assert reads == 1


def test_follow_ups_follow_the_registry_outcome() -> None:
    stub = StubRegistry({"Aria Shipping Lines": ARIA}, broken=frozenset({"Broken Co"}))
    aria, missing, broken = run(inv("Aria Shipping Lines"), inv("Nobody Co"), inv("Broken Co"),
                                stub=stub)  # fmt: skip
    assert isinstance(aria.follow_ups, Ran) and aria.follow_ups.value
    assert missing.follow_ups == Ran(())
    assert isinstance(broken.follow_ups, Failed)
    no_registry = RoutingRules(sources=IncludeExclude(exclude=frozenset({Source.REGISTRY})))
    [unchecked] = run(inv("Aria Shipping Lines"), rules=no_registry)
    assert unchecked.follow_ups is NOT_RUN


def test_source_rules_apply_to_follow_ups() -> None:
    no_media = RoutingRules(sources=IncludeExclude(exclude=frozenset({Source.MEDIA})))
    [aria] = run(inv("Aria Shipping Lines"), rules=no_media)
    rahimi = follow_ups(aria)["Dariush Rahimi"]
    assert rahimi.screening.media is NOT_RUN
    assert isinstance(rahimi.screening.sanctions, Ran) and rahimi.screening.sanctions.value


def test_entity_rules_select_primaries_only_follow_ups_are_still_screened() -> None:
    not_zagros = RoutingRules(names=IncludeExclude(exclude=frozenset({re.compile("^Zagros")})))
    reports = run(inv("Aria Shipping Lines"), inv("Zagros Freight"), rules=not_zagros)
    assert [r.entity for r in reports] == ["Aria Shipping Lines"]
    zagros = follow_ups(reports[0])["Zagros Freight"]
    assert isinstance(zagros.screening.media, Ran) and zagros.screening.media.value.count == 1


def test_registry_lookups_run_concurrently_and_keep_input_order() -> None:
    class SlowRegistry(StubRegistry):
        def __init__(self) -> None:
            super().__init__({})
            self.in_flight = self.peak = 0
            self.lock = threading.Lock()

        def lookup(self, name: str) -> RegistryRecord | None:
            with self.lock:
                self.in_flight += 1
                self.peak = max(self.peak, self.in_flight)
            time.sleep(0.05)
            with self.lock:
                self.in_flight -= 1
            return None

    slow = SlowRegistry()
    names = [f"Company {i}" for i in range(16)]
    started = time.perf_counter()
    reports = Orchestrator(
        WATCHLIST, lambda: ARTICLES, registry=CachingRegistry(slow), registry_workers=8
    ).run([inv(n) for n in names])
    elapsed = time.perf_counter() - started
    assert [r.entity for r in reports] == names
    assert slow.peak == 8
    assert elapsed < 16 * 0.05 / 2  # far below the sequential 0.8s


def test_text_matches_readme_follow_up_format() -> None:
    [aria] = run(inv("Aria Shipping Lines"))
    assert render_text([aria]) == (
        "Aria Shipping Lines:\n"
        "  sanctions: OFAC (High, IR)\n"
        "  registry: REG-IR-0042 (2 officers, 3 linked companies)\n"
        "  follow-ups:\n"
        "    Dariush Rahimi (officer): sanctions: EU (High, IR); media: 2 articles\n"
        "    Caspian Trade Bank (linked): sanctions: OFAC (High, IR); media: 1 article\n"
        "    Zagros Freight (linked): media: 1 article\n"
    )


def test_json_lists_every_follow_up() -> None:
    [aria] = run(inv("Aria Shipping Lines"))
    data = json.loads(render_json([aria]))
    names = [f["name"] for f in data["follow_ups"]]
    assert names == ["Farhad Noori", "Dariush Rahimi", "Caspian Trade Bank", "Zagros Freight"]
    farhad, rahimi, bank, _ = data["follow_ups"]
    assert farhad == {
        "name": "Farhad Noori",
        "kind": "officer",
        "roles": ["director"],
        "sanctions": [],
        "media": {"count": 0, "article_ids": []},
    }
    assert rahimi["sanctions"][0]["list"] == "EU"
    assert bank["kind"] == "linked" and bank["relations"] == ["subsidiary"]
