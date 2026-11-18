import json
from pathlib import Path

import pytest

from orchestrator.cli import main
from orchestrator.models import NOT_RUN, Article, Investigation, MediaFindings, Ran, Source
from orchestrator.orchestrator import Orchestrator
from orchestrator.sources.sanctions import Watchlist
from tests.conftest import article, sanction

WATCHLIST = Watchlist([sanction("Persian Gulf Petrochemical", "PGPC")])
ARTICLES = [article("AM-1", "Persian Gulf Petrochemical said"), article("AM-2", "PGPC said")]


def inv(name: str, country: str = "IR") -> Investigation:
    return Investigation(name=name, entity_type="organization", country=country)


def test_duplicate_investigations_are_reported_once_in_input_order() -> None:
    reports = Orchestrator(WATCHLIST, lambda: ARTICLES).run(
        [inv("B Co"), inv("A Co"), inv("b  co")]
    )
    assert [r.entity for r in reports] == ["B Co", "A Co"]


def test_media_search_uses_watchlist_aliases() -> None:
    [report] = Orchestrator(WATCHLIST, lambda: ARTICLES).run([inv("PGPC")])
    assert report.media == Ran(MediaFindings(("AM-1", "AM-2")))


def test_disabled_sources_are_not_run_and_media_is_not_read() -> None:
    def no_media() -> list[Article]:
        raise AssertionError("media archive must not be read")

    orch = Orchestrator(WATCHLIST, no_media, sources=frozenset({Source.SANCTIONS}))
    [report] = orch.run([inv("PGPC")])
    assert report.media is NOT_RUN
    assert isinstance(report.sanctions, Ran) and len(report.sanctions.value) == 1


def test_cli_text_output(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_paths(data_dir)) == 0
    assert capsys.readouterr().out == (
        "Aria Shipping Lines:\n"
        "  sanctions: OFAC (High, IR)\n"
        "  media: 2 articles (AM-1, AM-2)\n"
        "Northwind Logistics:\n"
        "  no findings\n"
        "PGPC:\n"
        "  sanctions: EU (High, IR)\n"
        "  media: 1 article (AM-3)\n"
    )


def test_cli_json_lines_output(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*_paths(data_dir), "--format", "json"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["entity"] for line in lines] == [
        "Aria Shipping Lines",
        "Northwind Logistics",
        "PGPC",
    ]
    assert lines[0]["sanctions"] == [
        {"list": "OFAC", "risk": "High", "country": "IR", "listed_name": "Aria Shipping Lines"}
    ]
    assert lines[0]["media"] == {"count": 2, "article_ids": ["AM-1", "AM-2"]}
    assert lines[1]["sanctions"] == [] and lines[1]["country"] == "US"
    assert lines[1]["source_status"] == {"sanctions": "ok", "media": "ok"}


def test_cli_reports_input_errors_with_exit_code_2(data_dir: Path) -> None:
    (data_dir / "sanctions.csv").write_text("bad header\n")
    assert main(_paths(data_dir)) == 2


def _paths(d: Path) -> list[str]:
    return [
        "--investigations",
        str(d / "investigations.csv"),
        "--sanctions",
        str(d / "sanctions.csv"),
        "--media",
        str(d / "media.jsonl.gz"),
    ]
