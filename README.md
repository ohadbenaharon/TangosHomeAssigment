# Investigation Orchestrator

Screens counterparties against a sanctions watchlist, an adverse-media archive and
a corporate registry. Design notes and decisions: [NOTES.md](NOTES.md).
The original assignment brief is in [ASSIGNMENT.md](ASSIGNMENT.md).

## Requirements

Python 3.12+. No runtime dependencies. [uv](https://docs.astral.sh/uv/) is used for
the dev tooling (pytest, mypy, ruff); plain `python3 -m orchestrator` also works.

## Run

```bash
uv sync
uv run python -m orchestrator                 # text report, default data/ paths
uv run python -m orchestrator --format json   # JSON Lines, one object per entity
uv run python -m orchestrator -v              # + progress/timing on stderr
```

Input paths can be overridden with `--investigations`, `--sanctions`, `--media`.

Exit codes: `0` success, `2` invalid input or arguments.

## Develop

```bash
uv run pytest
uv run mypy
uv run ruff check . && uv run ruff format --check .
uv run python scripts/crosscheck_media.py   # brute-force validation of media matching (~40s)
```
