from __future__ import annotations

import re

import pytest

from orchestrator.cli import build_parser, build_rules, main
from orchestrator.models import NOT_RUN, Article, Investigation, Ran, Source
from orchestrator.orchestrator import Orchestrator
from orchestrator.report import render_text
from orchestrator.routing import IncludeExclude, RoutingRules
from orchestrator.sources.registry import CachingRegistry
from orchestrator.sources.sanctions import Watchlist
from tests.conftest import sanction
from tests.test_orchestrator import StubRegistry


def inv(name: str, country: str) -> Investigation:
    return Investigation(name=name, entity_type="organization", country=country)


def media_must_not_run() -> list[Article]:
    raise AssertionError("media archive must not be read")


def rules(*argv: str) -> RoutingRules:
    return build_rules(build_parser().parse_args(list(argv)))


# --- IncludeExclude semantics ----------------------------------------------------


@pytest.mark.parametrize(
    ("include", "exclude", "value", "admitted"),
    [
        (set(), set(), "IR", True),  # no rules: everything included
        ({"IR", "RU"}, set(), "RU", True),  # includes are OR'd
        ({"IR", "RU"}, set(), "US", False),
        (set(), {"US", "AE"}, "AE", False),  # excludes are OR'd
        (set(), {"US"}, "IR", True),
        ({"IR"}, {"IR"}, "IR", False),  # exclude beats include
    ],
)
def test_include_exclude_semantics(
    include: set[str], exclude: set[str], value: str, admitted: bool
) -> None:
    rule = IncludeExclude(frozenset(include), frozenset(exclude))
    assert rule.admits(lambda item: item == value) is admitted


# --- RoutingRules ----------------------------------------------------------------


def test_default_rules_admit_everything_and_enable_all_sources() -> None:
    assert RoutingRules().admits(inv("Anything", "XX"))
    assert RoutingRules().enabled_sources() == frozenset(Source)


def test_source_include_and_exclude() -> None:
    assert rules("--include-source", "sanctions,registry").enabled_sources() == {
        Source.SANCTIONS,
        Source.REGISTRY,
    }
    assert rules("--exclude-source", "media").enabled_sources() == {
        Source.SANCTIONS,
        Source.REGISTRY,
    }
    assert rules(
        "--include-source", "sanctions", "--include-source", "media", "--exclude-source", "media"
    ).enabled_sources() == {Source.SANCTIONS}


def test_country_rules_are_case_insensitive() -> None:
    r = rules("--include-country", "ir, ru", "--exclude-country", "RU")
    assert r.admits(inv("A", "IR"))
    assert r.admits(inv("A", "ir"))
    assert not r.admits(inv("A", "RU"))
    assert not r.admits(inv("A", "US"))


def test_name_rules_use_unanchored_case_sensitive_search() -> None:
    r = rules("--include-name", "Shipping", "--include-name", "^Caspian", "--exclude-name", "^Test")
    assert r.admits(inv("Aria Shipping Lines", "IR"))
    assert r.admits(inv("Caspian Trade Bank", "IR"))
    assert not r.admits(inv("Test Shipping Co", "IR"))  # exclude beats include
    assert not r.admits(inv("aria shipping lines", "IR"))  # case-sensitive
    assert rules("--include-name", "(?i)shipping").admits(inv("aria shipping", "IR"))


def test_name_regex_with_comma_is_not_split() -> None:
    r = rules("--include-name", "^A{1,2} Co$")
    assert r.names.include == {re.compile("^A{1,2} Co$")}
    assert r.admits(inv("AA Co", "IR"))


@pytest.mark.parametrize(
    "argv",
    [
        ["--include-source", "sanctions,bogus"],
        ["--exclude-name", "("],
        ["--exclude-source", "sanctions,media,registry"],
    ],
)
def test_invalid_routing_flags_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


# --- the README's combined example, end to end -----------------------------------


def test_spec_combined_example() -> None:
    """Include only sources sanctions and registry, restricted to countries IR,RU,
    excluding entities matching ^Test, while excluding country US."""
    r = rules(
        "--include-source", "sanctions,registry",
        "--include-country", "IR,RU",
        "--exclude-name", "^Test",
        "--exclude-country", "US",
    )  # fmt: skip
    stub = StubRegistry({})
    orch = Orchestrator(
        Watchlist([sanction("Ural Petro Holdings", country="RU")]),
        articles=media_must_not_run,
        registry=CachingRegistry(stub),
        rules=r,
    )
    reports = orch.run(
        [
            inv("Aria Shipping Lines", "IR"),
            inv("Ural Petro Holdings", "RU"),
            inv("Test Holdings", "IR"),  # excluded by name
            inv("Northwind Logistics", "US"),  # excluded by country (and not included)
            inv("Damavand Commodities", "AE"),  # not in included countries
        ]
    )
    assert [rep.entity for rep in reports] == ["Aria Shipping Lines", "Ural Petro Holdings"]
    assert stub.calls == ["Aria Shipping Lines", "Ural Petro Holdings"]
    assert all(rep.media is NOT_RUN for rep in reports)
    assert all(isinstance(rep.sanctions, Ran) for rep in reports)
    assert render_text(reports) == (
        "Aria Shipping Lines:\n"
        "  no findings (checked: sanctions, registry)\n"
        "Ural Petro Holdings:\n"
        "  sanctions: OFAC (High, RU)\n"
    )
