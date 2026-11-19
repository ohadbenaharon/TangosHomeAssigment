"""Independently re-derive a full default run and diff it against the orchestrator.

Usage:
    uv run python -m orchestrator --format json > report.jsonl
    uv run python scripts/validate_report.py report.jsonl          # ~1 minute

Deliberately shares no code with the orchestrator: it reads the raw files
directly (registry.json instead of the HTTP service, csv for the watchlist) and
finds media mentions with plain per-name regexes, implementing the "not inside a
longer known name" rule its own way (span containment between regex matches).
Every field of every entity and follow-up is compared; mismatches are printed.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

DATA = Path("data")


def norm(name: str) -> str:
    return " ".join(re.findall(r"\w+", name.casefold()))


def main(report_path: str) -> int:
    report = [json.loads(line) for line in Path(report_path).read_text().splitlines()]

    # --- raw data -----------------------------------------------------------------
    watch_rows = list(csv.DictReader((DATA / "sanctions.csv").open()))
    watch: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
    for row in watch_rows:
        names = [row["Entity name"], *(a for a in row["Aliases"].split("|") if a.strip())]
        entry = (row["List"], row["Risk"], row["Country of origin"], row["Entity name"])
        for name in names:
            watch[norm(name)].add(entry)
    aliases: dict[str, set[str]] = defaultdict(set)  # name -> all names of matched rows
    for row in watch_rows:
        group = {norm(row["Entity name"])} | {
            norm(a) for a in row["Aliases"].split("|") if a.strip()
        }
        for name in group:
            aliases[name] |= group

    registry: dict[str, dict[str, Any]] = {}
    for rec in json.loads((DATA / "registry.json").read_text()):
        for name in [rec["name"], *rec["aliases"]]:
            registry[norm(name)] = rec

    investigated: list[str] = []
    for row in csv.DictReader((DATA / "investigations.csv").open()):
        if norm(row["Entity name"]) not in map(norm, investigated):
            investigated.append(row["Entity name"])

    def follow_up_names(rec: dict[str, Any]) -> list[tuple[str, str]]:
        seen: dict[str, tuple[str, str]] = {}
        for o in rec["officers"]:
            seen.setdefault(norm(o["name"]), (o["name"], "officer"))
        for link in rec["links"]:
            seen.setdefault(norm(link["name"]), (link["name"], "linked"))
        seen.pop(norm(rec["name"]), None)
        return list(seen.values())

    # --- media, the slow obvious way ------------------------------------------------
    screened = {norm(n) for n in investigated}
    for n in investigated:
        if (rec := registry.get(norm(n))) is not None:
            screened |= {norm(f) for f, _ in follow_up_names(rec)}
    universe = screened | set(watch)
    for name in list(screened):
        universe |= aliases.get(name, set())
    patterns = {
        n: re.compile(r"\b" + r"\W+".join(map(re.escape, n.split())) + r"\b") for n in universe
    }
    mentions: dict[str, set[str]] = defaultdict(set)
    with gzip.open(DATA / "media.jsonl.gz", "rt") as fh:
        for line in fh:
            art = json.loads(line)
            for text in (art["title"], art["body"]):
                low = text.casefold()
                spans = {
                    n: [m.span() for m in p.finditer(low)]
                    for n, p in patterns.items()
                    if n.split()[0] in low
                }
                for n, own in spans.items():
                    for s, e in own:
                        nested = any(
                            (os_ <= s and e <= oe) and (os_, oe) != (s, e)
                            for other, other_spans in spans.items()
                            if other != n
                            for os_, oe in other_spans
                        )
                        if not nested:
                            mentions[n].add(art["id"])

    def expected_screening(name: str) -> dict[str, Any]:
        ids: set[str] = set()
        for alias in aliases.get(norm(name), {norm(name)}) | {norm(name)}:
            ids |= mentions.get(alias, set())
        return {
            "sanctions": sorted(
                [
                    {"list": li, "risk": r, "country": c, "listed_name": n}
                    for li, r, c, n in watch.get(norm(name), set())
                ],
                key=lambda d: (d["list"], d["risk"], d["country"], d["listed_name"]),
            ),
            "media": {"count": len(ids), "article_ids": sorted(ids)},
        }

    # --- compare --------------------------------------------------------------------
    problems = 0

    def check(where: str, got: Any, want: Any) -> None:
        nonlocal problems
        if got != want:
            problems += 1
            print(f"MISMATCH {where}:\n  got  {got}\n  want {want}")

    check("entity list", [r["entity"] for r in report], investigated)
    follow_up_total = 0
    for entry in report:
        name = entry["entity"]
        want = expected_screening(name)
        check(f"{name} sanctions", entry["sanctions"], want["sanctions"])
        check(f"{name} media", entry["media"], want["media"])
        rec = registry.get(norm(name))
        if rec is None:
            check(f"{name} registry", entry["registry"], {"found": False})
            check(f"{name} follow_ups", entry["follow_ups"], [])
            continue
        check(f"{name} registry id", entry["registry"]["id"], rec["id"])
        check(
            f"{name} officer_count",
            entry["registry"]["officer_count"],
            len({norm(o["name"]) for o in rec["officers"]}),
        )
        check(f"{name} linked_count", entry["registry"]["linked_count"], len(rec["links"]))
        expected_fus = follow_up_names(rec)
        got_fus = entry["follow_ups"]
        check(f"{name} follow-up names", [(f["name"], f["kind"]) for f in got_fus], expected_fus)
        for fu in got_fus:
            follow_up_total += 1
            want_fu = expected_screening(fu["name"])
            check(f"{name} -> {fu['name']} sanctions", fu["sanctions"], want_fu["sanctions"])
            check(f"{name} -> {fu['name']} media", fu["media"], want_fu["media"])

    print(
        f"checked {len(report)} entities, {follow_up_total} follow-up entries "
        f"({len(screened)} distinct names screened): {problems} mismatches"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
