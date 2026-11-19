import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from orchestrator.cli import main
from orchestrator.models import (
    NOT_RUN,
    Article,
    Failed,
    Investigation,
    MediaFindings,
    Ran,
    RegistryFindings,
    RegistryRecord,
    Source,
)
from orchestrator.orchestrator import Orchestrator
from orchestrator.routing import IncludeExclude, RoutingRules
from orchestrator.sources.registry import CachingRegistry, RegistryError
from orchestrator.sources.sanctions import Watchlist
from tests.conftest import article, sanction
from tests.fake_registry import FakeRegistry, record, serve

WATCHLIST = Watchlist([sanction("Persian Gulf Petrochemical", "PGPC")])
ARTICLES = [article("AM-1", "Persian Gulf Petrochemical said"), article("AM-2", "PGPC said")]
LOCAL = RoutingRules(sources=IncludeExclude(exclude=frozenset({Source.REGISTRY})))


class StubRegistry:
    def __init__(self, records: dict[str, RegistryRecord], broken: frozenset[str] = frozenset()):
        self.records, self.broken, self.calls = records, broken, list[str]()

    def lookup(self, name: str) -> RegistryRecord | None:
        self.calls.append(name)
        if name in self.broken:
            raise RegistryError("boom")
        return self.records.get(name)


def inv(name: str, country: str = "IR") -> Investigation:
    return Investigation(name=name, entity_type="organization", country=country)


def test_duplicate_investigations_are_reported_once_in_input_order() -> None:
    reports = Orchestrator(WATCHLIST, lambda: ARTICLES, rules=LOCAL).run(
        [inv("B Co"), inv("A Co"), inv("b  co")]
    )
    assert [r.entity for r in reports] == ["B Co", "A Co"]


def test_media_search_uses_watchlist_aliases() -> None:
    [report] = Orchestrator(WATCHLIST, lambda: ARTICLES, rules=LOCAL).run([inv("PGPC")])
    assert report.media == Ran(MediaFindings(("AM-1", "AM-2")))


def test_disabled_sources_are_not_run_and_media_is_not_read() -> None:
    def no_media() -> list[Article]:
        raise AssertionError("media archive must not be read")

    only_sanctions = RoutingRules(sources=IncludeExclude(include=frozenset({Source.SANCTIONS})))
    orch = Orchestrator(WATCHLIST, no_media, rules=only_sanctions)
    [report] = orch.run([inv("PGPC")])
    assert report.media is NOT_RUN and report.registry is NOT_RUN
    assert isinstance(report.sanctions, Ran) and len(report.sanctions.value) == 1


def test_registry_enabled_without_client_is_rejected() -> None:
    with pytest.raises(ValueError, match="registry"):
        Orchestrator(WATCHLIST, lambda: ARTICLES)


def test_registry_outcomes_found_missing_and_failed() -> None:
    found = RegistryRecord("REG-1", "A Co", "IR", (), ())
    stub = StubRegistry({"A Co": found}, broken=frozenset({"C Co"}))
    orch = Orchestrator(WATCHLIST, lambda: ARTICLES, registry=CachingRegistry(stub))
    a, b, c = orch.run([inv("A Co"), inv("B Co"), inv("C Co"), inv("a co")])
    assert a.registry == Ran(RegistryFindings(found))
    assert b.registry == Ran(RegistryFindings(None))
    assert isinstance(c.registry, Failed)
    assert stub.calls == ["A Co", "B Co", "C Co"]


# --- end-to-end through the CLI, against an in-process HTTP registry --------------


@pytest.fixture
def registry() -> Iterator[FakeRegistry]:
    aria = record(
        "REG-IR-0042",
        "Aria Shipping Lines",
        [(f"Officer {i}", "director") for i in range(7)],
        [("Caspian Trade Bank", "subsidiary"), ("Zagros Freight", "shareholder")],
    )
    pgpc = record("REG-AE-0001", "PGPC", [("Solo Person", "director")], country="AE")
    with serve(FakeRegistry(records=[aria, pgpc])) as fake:
        yield fake


def test_cli_text_output(
    data_dir: Path, registry: FakeRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_args(data_dir, registry)) == 0
    assert capsys.readouterr().out == (
        "Aria Shipping Lines:\n"
        "  sanctions: OFAC (High, IR)\n"
        "  media: 2 articles (AM-1, AM-2)\n"
        "  registry: REG-IR-0042 (7 officers, 2 linked companies)\n"
        "Northwind Logistics:\n"
        "  no findings\n"
        "PGPC:\n"
        "  sanctions: EU (High, IR)\n"
        "  media: 1 article (AM-3)\n"
        "  registry: REG-AE-0001 (1 officer, 0 linked companies; registered in AE)\n"
    )


def test_cli_json_lines_output(
    data_dir: Path, registry: FakeRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*_args(data_dir, registry), "--format", "json"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["entity"] for line in lines] == [
        "Aria Shipping Lines",
        "Northwind Logistics",
        "PGPC",
    ]
    aria, northwind, _ = lines
    assert aria["sanctions"] == [
        {"list": "OFAC", "risk": "High", "country": "IR", "listed_name": "Aria Shipping Lines"}
    ]
    assert aria["media"] == {"count": 2, "article_ids": ["AM-1", "AM-2"]}
    assert aria["registry"]["id"] == "REG-IR-0042"
    assert (aria["registry"]["officer_count"], aria["registry"]["linked_count"]) == (7, 2)
    assert aria["registry"]["linked"][0] == {
        "name": "Caspian Trade Bank",
        "relations": ["subsidiary"],
    }
    assert northwind["sanctions"] == [] and northwind["country"] == "US"
    assert northwind["registry"] == {"found": False}
    assert northwind["source_status"] == {"sanctions": "ok", "media": "ok", "registry": "ok"}


def test_cli_registry_failure_is_reported_and_exits_1(
    data_dir: Path, registry: FakeRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    registry.fail_with = [400]  # first lookup (Aria Shipping Lines) fails, not retried
    assert main([*_args(data_dir, registry), "--format", "json"]) == 1
    aria = json.loads(capsys.readouterr().out.splitlines()[0])
    assert aria["registry"] is None
    assert aria["source_status"]["registry"] == "error"
    assert "HTTP 400" in aria["errors"]["registry"]


def test_cli_reports_input_errors_with_exit_code_2(data_dir: Path, registry: FakeRegistry) -> None:
    (data_dir / "sanctions.csv").write_text("bad header\n")
    assert main(_args(data_dir, registry)) == 2


def test_cli_warns_when_routing_rules_match_nothing(
    data_dir: Path, registry: FakeRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty report must not read as "screened and clean" when nothing was screened."""
    assert main([*_args(data_dir, registry), "--include-name", "^Nobody$"]) == 0
    assert "nothing was screened" in caplog.text


def test_cli_name_screens_ad_hoc_names_instead_of_the_file(
    data_dir: Path, registry: FakeRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _without_investigations(_args(data_dir, registry))
    assert main([*args, "--name", "PGPC", "--name", "Someone New"]) == 0
    assert capsys.readouterr().out == (
        "PGPC:\n"
        "  sanctions: EU (High, IR)\n"
        "  media: 1 article (AM-3)\n"
        # country unknown for an ad-hoc name: no misleading "registered in AE" note
        "  registry: REG-AE-0001 (1 officer, 0 linked companies)\n"
        "Someone New:\n"
        "  no findings\n"
    )


@pytest.mark.parametrize(
    "extra",
    [["--name=---"], ["--name", " "], ["--name", "X", "--investigations", "i.csv"]],
)
def test_cli_rejects_bad_name_usage(
    data_dir: Path, registry: FakeRegistry, extra: list[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main([*_without_investigations(_args(data_dir, registry)), *extra])
    assert exc.value.code == 2


def _without_investigations(args: list[str]) -> list[str]:
    i = args.index("--investigations")
    return args[:i] + args[i + 2 :]


def _args(d: Path, registry: FakeRegistry) -> list[str]:
    return [
        "--investigations",
        str(d / "investigations.csv"),
        "--sanctions",
        str(d / "sanctions.csv"),
        "--media",
        str(d / "media.jsonl.gz"),
        "--registry-url",
        registry.url,
    ]
