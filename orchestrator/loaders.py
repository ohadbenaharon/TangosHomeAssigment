"""Parsing of the input files into domain models.

Malformed input fails loudly with the file and line number. For a screening tool,
silently skipping a bad watchlist row or article would mean silently missing a hit.
"""

from __future__ import annotations

import csv
import gzip
import json
import zlib
from collections.abc import Iterator
from pathlib import Path

from orchestrator.models import Article, Investigation, SanctionEntry
from orchestrator.names import name_key


class InputError(Exception):
    """An input file is missing, unreadable or malformed."""


INVESTIGATION_COLUMNS = ("Entity name", "Entity type", "Country")
SANCTION_COLUMNS = ("Entity name", "Aliases", "Risk", "Country of origin", "List")
ARTICLE_FIELDS = ("id", "published", "title", "body")


def load_investigations(path: Path) -> list[Investigation]:
    return _non_empty(
        path,
        "investigations",
        [
            Investigation(
                name=_name(row, "Entity name", path, line),
                entity_type=row["Entity type"],
                country=row["Country"].upper(),
            )
            for line, row in _read_csv(path, INVESTIGATION_COLUMNS)
        ],
    )


def load_sanctions(path: Path) -> list[SanctionEntry]:
    return _non_empty(
        path,
        "watchlist entries",
        [
            SanctionEntry(
                name=_name(row, "Entity name", path, line),
                aliases=tuple(a.strip() for a in row["Aliases"].split("|") if a.strip()),
                risk=row["Risk"],
                country=row["Country of origin"].upper(),
                list_name=_required(row, "List", path, line),
            )
            for line, row in _read_csv(path, SANCTION_COLUMNS)
        ],
    )


def iter_articles(path: Path) -> Iterator[Article]:
    """Stream articles from a gzipped JSON-lines file (never fully held in memory)."""
    count = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    article = Article(**{f: str(raw[f]) for f in ARTICLE_FIELDS})
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise InputError(f"{path}:{line_no}: malformed article ({exc})") from exc
                count += 1
                yield article
    # missing file, not gzip, truncated or corrupt archive, not UTF-8
    except (OSError, EOFError, zlib.error, UnicodeDecodeError) as exc:
        raise InputError(f"{path}: cannot read media archive ({exc})") from exc
    if not count:
        raise InputError(f"{path}: media archive has no articles")


def _read_csv(path: Path, columns: tuple[str, ...]) -> Iterator[tuple[int, dict[str, str]]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh, skipinitialspace=True)
            header = [h.strip() for h in reader.fieldnames or ()]
            missing = [c for c in columns if c not in header]
            if missing:
                raise InputError(f"{path}: missing column(s) {missing}; found {header}")
            reader.fieldnames = header
            for row in reader:
                if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
                    continue  # blank line
                if None in row or None in row.values():
                    raise InputError(
                        f"{path}:{reader.line_num}: expected {len(header)} fields, "
                        f"got a row with {'more' if None in row else 'fewer'}"
                    )
                yield reader.line_num, {c: (row.get(c) or "").strip() for c in columns}
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise InputError(f"{path}: cannot read ({exc})") from exc


def _non_empty[T](path: Path, what: str, rows: list[T]) -> list[T]:
    """An empty file is far more likely a broken export than a real empty batch; for a
    watchlist it would silently report every entity as clean."""
    if not rows:
        raise InputError(f"{path}: no {what} (file has only a header, or is empty)")
    return rows


def _name(row: dict[str, str], column: str, path: Path, line: int) -> str:
    """A name must contain at least one word character: names are compared by their
    word tokens, so e.g. "---" and "&&&" would otherwise silently become one entity."""
    value = _required(row, column, path, line)
    if not name_key(value):
        raise InputError(f"{path}:{line}: {column!r} {value!r} has no letters or digits")
    return value


def _required(row: dict[str, str], column: str, path: Path, line: int) -> str:
    value = row[column]
    if not value:
        raise InputError(f"{path}:{line}: empty {column!r}")
    return value
