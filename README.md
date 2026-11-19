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

The registry service must be running for registry checks:

```bash
python3 registry_service.py      # listens on localhost:7311
```

Input paths can be overridden with `--investigations`, `--sanctions`, `--media`;
the registry with `--registry-url` and `--registry-timeout`.

### Routing rules

```bash
# only sanctions + registry, for IR/RU entities, skipping names starting "Test" and US
uv run python -m orchestrator --include-source sanctions,registry \
    --include-country IR,RU --exclude-name '^Test' --exclude-country US
```

`--include-/--exclude-source`, `--include-/--exclude-country`, `--include-/--exclude-name`
(regex, `re.search`, case-sensitive unless `(?i)`). Flags repeat; within one dimension
rules are OR'd and exclude beats include; dimensions are AND'd. See `--help`.

Exit codes: `0` success; `1` report produced but some source checks failed (e.g. the
registry was unreachable; affected entities show `ERROR`); `2` invalid input or arguments.

## Develop

```bash
uv run pytest
uv run mypy
uv run ruff check . && uv run ruff format --check .
uv run python scripts/crosscheck_media.py   # brute-force validation of media matching (~40s)
```
