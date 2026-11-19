"""Command-line entry point. The only module that knows about argv, stdout and exit codes."""

from __future__ import annotations

import argparse
import functools
import logging
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from orchestrator.loaders import InputError, iter_articles, load_investigations, load_sanctions
from orchestrator.models import EntityReport, Failed, Source
from orchestrator.orchestrator import DEFAULT_REGISTRY_WORKERS, Orchestrator
from orchestrator.report import render_json, render_text
from orchestrator.routing import IncludeExclude, RoutingRules
from orchestrator.sources.registry import CachingRegistry, RegistryClient
from orchestrator.sources.sanctions import Watchlist

log = logging.getLogger("orchestrator")

EXIT_OK = 0
EXIT_SOURCE_ERROR = 1  # report produced, but at least one check failed
EXIT_INPUT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orchestrator",
        description="Screen counterparties against sanctions, adverse media and the registry.",
    )
    p.add_argument("--investigations", type=Path, default=Path("data/investigations.csv"))
    p.add_argument("--sanctions", type=Path, default=Path("data/sanctions.csv"))
    p.add_argument("--media", type=Path, default=Path("data/media.jsonl.gz"))
    p.add_argument("--registry-url", default="http://localhost:7311")
    p.add_argument(
        "--registry-timeout",
        type=_positive_float,
        default=5.0,
        metavar="SECONDS",
        help="per request (default: %(default)s)",
    )
    p.add_argument(
        "--registry-workers",
        type=_positive_int,
        default=DEFAULT_REGISTRY_WORKERS,
        metavar="N",
        help="concurrent registry requests (default: %(default)s)",
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")

    routing = p.add_argument_group(
        "routing rules",
        "Each flag may be repeated; source/country values may be comma-separated. Within "
        "one dimension rules are OR'd and exclude beats include; dimensions are AND'd. "
        "With no include rule for a dimension, everything is included for it.",
    )
    sources = ",".join(s.value for s in Source)
    for verb in ("include", "exclude"):
        routing.add_argument(f"--{verb}-source", action="append", default=[], metavar=sources)
        routing.add_argument(f"--{verb}-country", action="append", default=[], metavar="CC,...")
        routing.add_argument(
            f"--{verb}-name",
            action="append",
            default=[],
            metavar="REGEX",
            help="re.search on the entity name; case-sensitive unless (?i)",
        )
    return p


def build_rules(args: argparse.Namespace) -> RoutingRules:
    """Translate routing flags into rules; raises ValueError on invalid values."""
    return RoutingRules(
        sources=IncludeExclude(
            include=frozenset(map(_source, _split(args.include_source))),
            exclude=frozenset(map(_source, _split(args.exclude_source))),
        ),
        countries=IncludeExclude(
            include=frozenset(c.upper() for c in _split(args.include_country)),
            exclude=frozenset(c.upper() for c in _split(args.exclude_country)),
        ),
        names=IncludeExclude(
            include=frozenset(map(_pattern, args.include_name)),
            exclude=frozenset(map(_pattern, args.exclude_name)),
        ),
    )


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not number > 0:  # also rejects NaN
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _split(values: list[str]) -> list[str]:
    # Regexes are not split: a comma is a legal regex character.
    return [v.strip() for value in values for v in value.split(",") if v.strip()]


def _source(value: str) -> Source:
    try:
        return Source(value.lower())
    except ValueError:
        raise ValueError(
            f"unknown source {value!r} (expected one of: {', '.join(Source)})"
        ) from None


def _pattern(value: str) -> re.Pattern[str]:
    try:
        return re.compile(value)
    except re.error as exc:
        raise ValueError(f"invalid regular expression {value!r}: {exc}") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rules = build_rules(args)
    except ValueError as exc:
        parser.error(str(exc))  # exits with code 2
    if not rules.enabled_sources():
        parser.error("routing rules exclude every source; nothing would be checked")
    try:
        client = RegistryClient(args.registry_url, timeout_seconds=args.registry_timeout)
    except ValueError as exc:
        parser.error(f"--registry-url: {exc}")
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    started = time.perf_counter()
    try:
        investigations = load_investigations(args.investigations)
        orchestrator = Orchestrator(
            watchlist=Watchlist(load_sanctions(args.sanctions)),
            articles=functools.partial(iter_articles, args.media),
            registry=CachingRegistry(client),
            rules=rules,
            registry_workers=args.registry_workers,
        )
        reports = orchestrator.run(investigations)
    except InputError as exc:
        log.error("%s", exc)
        return EXIT_INPUT_ERROR

    render = render_json if args.format == "json" else render_text
    sys.stdout.write(render(reports))
    log.info("sources: %s", ", ".join(sorted(rules.enabled_sources())))
    log.info(
        "screened %d distinct entities (%d input rows) in %.2fs",
        len(reports),
        len(investigations),
        time.perf_counter() - started,
    )
    failed = sum(1 for r in reports if _has_failure(r))
    if failed:
        log.error("%d entities have incomplete results (failed source checks)", failed)
        return EXIT_SOURCE_ERROR
    return EXIT_OK


def _has_failure(report: EntityReport) -> bool:
    return any(isinstance(o, Failed) for o in report.outcomes.values())


if __name__ == "__main__":
    sys.exit(main())
