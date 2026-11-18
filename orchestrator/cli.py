"""Command-line entry point. The only module that knows about argv, stdout and exit codes."""

from __future__ import annotations

import argparse
import functools
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from orchestrator.loaders import InputError, iter_articles, load_investigations, load_sanctions
from orchestrator.orchestrator import Orchestrator
from orchestrator.report import render_json, render_text
from orchestrator.sources.sanctions import Watchlist

log = logging.getLogger("orchestrator")

EXIT_OK = 0
EXIT_INPUT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orchestrator",
        description="Screen counterparties against sanctions, adverse media and the registry.",
    )
    p.add_argument("--investigations", type=Path, default=Path("data/investigations.csv"))
    p.add_argument("--sanctions", type=Path, default=Path("data/sanctions.csv"))
    p.add_argument("--media", type=Path, default=Path("data/media.jsonl.gz"))
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
        )
        reports = orchestrator.run(investigations)
    except InputError as exc:
        log.error("%s", exc)
        return EXIT_INPUT_ERROR

    render = render_json if args.format == "json" else render_text
    sys.stdout.write(render(reports))
    log.info(
        "screened %d distinct entities (%d input rows) in %.2fs",
        len(reports),
        len(investigations),
        time.perf_counter() - started,
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
