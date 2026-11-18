"""Independent cross-check of media matching against a naive brute-force regex.

For every investigated entity, search every article with a plain
case-insensitive ``\\b<phrase>\\b`` regex for each of the entity's names, and
compare with the orchestrator's results. The only legitimate differences are
mentions nested inside a longer known name (the README's "Aria Shipping" rule);
each difference is printed with the containing name so it can be eyeballed.

Run: uv run python scripts/crosscheck_media.py   (takes ~1 minute)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from orchestrator.loaders import iter_articles, load_investigations, load_sanctions
from orchestrator.models import Ran
from orchestrator.orchestrator import Orchestrator, dedupe
from orchestrator.sources.sanctions import Watchlist

DATA = Path("data")


def main() -> int:
    watchlist = Watchlist(load_sanctions(DATA / "sanctions.csv"))
    entities = dedupe(load_investigations(DATA / "investigations.csv"))
    articles = list(iter_articles(DATA / "media.jsonl.gz"))
    reports = Orchestrator(watchlist, lambda: articles).run(entities)

    all_names = sorted({" ".join(k) for k in watchlist.names | {e.key for e in entities}})
    mismatches = 0
    for entity, report in zip(entities, reports, strict=True):
        assert isinstance(report.media, Ran)
        ours = set(report.media.value.article_ids)
        patterns = [
            re.compile(r"\b" + r"\s+".join(map(re.escape, key)) + r"\b", re.IGNORECASE)
            for key in watchlist.aliases_of(entity.key)
        ]
        naive = {
            a.id for a in articles if any(p.search(a.title) or p.search(a.body) for p in patterns)
        }
        if ours == naive:
            continue
        mismatches += 1
        longer = [
            n
            for n in all_names
            if n != " ".join(entity.key)
            and re.search(r"\b" + re.escape(" ".join(entity.key)) + r"\b", n)
        ]
        print(
            f"{entity.name}: ours={len(ours)} naive={len(naive)} "
            f"only_naive={sorted(naive - ours)[:5]} only_ours={sorted(ours - naive)[:5]} "
            f"longer_known_names={longer}"
        )
    print(f"{len(entities)} entities checked, {mismatches} differ from naive regex")
    return 0


if __name__ == "__main__":
    sys.exit(main())
