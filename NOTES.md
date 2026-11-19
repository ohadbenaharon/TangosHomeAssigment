# NOTES

## Design

A phased pipeline (`orchestrator/orchestrator.py`). The phases are explicit because
the media scan needs every name up front, and follow-up names only exist once the
registry has answered:

1. **Route & de-duplicate**: drop rows the rules exclude, merge repeated entities.
2. **Registry fan-out**: concurrent lookups through a thread pool.
3. **Collect follow-ups**: officers and linked companies of the records found.
4. **Screen every distinct name once**: watchlist lookup, plus **one** pass over the
   media archive for all names together.
5. **Assemble** reports in input order; render as text or JSON Lines.

- `names.py` is the single definition of "same name" (NFKC + casefold + word tokens),
  used by every source, the cache and de-duplication, so the rule can't drift.
- `matching.py`: `PhraseIndex`, a dictionary matcher over token n-grams (linear in
  text length, not names × text).
- `models.py`: every source result is `Ran[T] | NotRun | Failed`, so "checked,
  nothing found", "not checked" and "check failed" can never be confused. This is
  the property I care most about in a screening tool.
- `routing.py`: one `IncludeExclude[T]` shared by the source, country and name rules.
- `cli.py` is the only module that knows about argv, stdout and exit codes.

Stdlib only at runtime; pytest, mypy `--strict` and ruff for development.

Trade-offs

Why threads for the registry: the registry is slow (~300ms per request) and a run
needs 108 requests, so one at a time took 34s. The program is just waiting on the
network, not computing. Python releases the GIL while a thread waits on a socket,
so a pool of 16 threads runs 16 requests at once: 3.6s for the whole run.
- Not asyncio: the standard library has no async HTTP client, so I'd need a
  dependency like aiohttp, and every function on the path would have to become
  `async`. A thread pool keeps the code synchronous and easy to test.
- Not processes: there's no CPU work to spread out, and processes cost more to start.
- 16 workers: fast enough, and modest load on a shared service. At 64, the service
  starts dropping connections.

The media scan (~1.1s) is CPU work, so threads wouldn't speed it up (GIL), and
processes would add more complexity than the second they'd save. It also can't
overlap the registry phase, because it needs the follow-up names first.

## Decisions where the spec left room

Matching
- A plain `\b` regex isn't enough: `\bAria Shipping\b` also matches inside "Aria
  Shipping Lines". I drop a match when it sits inside a longer known name.
- Watchlist aliases also count in the media search (`PGPC` finds "Persian Gulf
  Petrochemical"). One hop only, so unrelated entities don't get chained.

Registry
- `API.md` doesn't match the service. The bulk endpoint always returns 404, so I
  don't use it, and officers are paginated, so I fetch every page.
- Officer count is distinct people. One person with two roles counts once.
- A failed lookup is reported as `ERROR` with exit code 1, never as "no record".
  In screening, "couldn't check" must not look like "clean". After 5 failures in a
  row a circuit breaker fails the rest fast, still as errors.
- If the registry country differs from the investigated one, I show it
  (`registered in XX`) instead of dropping the record. It may be a namesake, but
  that's for the analyst to decide.

Routing
- Exclude beats include: an explicit "never" shouldn't be undone by a broader "only".
- Country and name rules pick the primary entities only. Follow-ups have no
  country, and a sanctioned director should be found wherever the company is.

Follow-ups
- Screened once per distinct name across the whole run, and never sent back to the
  registry (depth 1).
- The text report shows only follow-ups with findings, like the README example.
  JSON has all of them.

Output and input
- Duplicate rows in the investigations file are merged (150 rows, 97 entities).
- JSON is JSON Lines, one entity per line.
- `--name` checks a typed name without a CSV file.
- Bad or empty input files stop the run (exit 2). An empty watchlist would
  otherwise report everyone as clean.

## Concurrency and performance

| run | wall time |
|---|---|
| checkpoint 2 (sequential registry) | 34s |
| **full default run (16 workers)** | **3.6s**: registry 2.5s, media scan 1.1s |
| registry service down | 1.4s, all entities reported as errors |
| registry host unreachable (packets dropped) | 25s (~24 min without the breaker) |

The registry phase is bounded by its ~300ms per request (108 requests). Reaching 16
workers needed two measured transport fixes:

- **urllib's macOS proxy lookup burned ~14s of CPU per run** under threads. Switched
  to `http.client`, which skips it: 0.1s of CPU.
- **The service's listen backlog is 5**, and it closes every connection after one
  response, so reconnect bursts got `ECONNRESET` (8–9 per run). **Opening connections
  one at a time** (a lock around `connect()` only; requests still overlap) gives 0
  resets at the same speed. Its cost: with an unreachable host every connect times
  out in turn, which the circuit breaker caps.

## Validation

- **92 tests** (0.6s), including a contract test against the provided
  `registry_service.Handler`, an in-process fake registry with failure injection,
  shared in-flight requests, and the README's examples end to end.
- **`scripts/validate_report.py`** re-derives a full run independently: raw files
  instead of HTTP, per-name regexes, its own nested-name logic. It diffs every field:
  **97 entities, 242 follow-ups, 0 mismatches.** It caught all three bugs I planted
  in a copy of the report, so it isn't vacuous.
- A plain `\b` regex differs from ours on exactly one entity, `Aria Shipping`
  (21 vs 4). The 17 extra articles all mention "Aria Shipping Lines"; I read the
  other 4 by hand.
- The README examples reproduce on real data. The two article ids in its example
  (`AM-004512`, `AM-008133`) are placeholders that don't mention Aria; a raw search
  finds exactly our 17.
- Three consecutive runs: byte-identical output, 0 retries.

## Surprises
- The README's own matching example contradicts plain `\b` regex semantics.
- `API.md` doesn't describe the service: the bulk endpoint 404s, officers are
  paginated, the field is `links`, not `linked_entities`.
- The slow urllib proxy lookup was hiding the connection-reset problem; fixing the
  first exposed the second.

## Time spent

Built with an AI assistant (Claude Code). Most of the time went into design and
review, not typing:
- Architecture and reading the brief: ~1h
- Checkpoints 1–3: ~10 min each
- Checkpoint 4: ~20 min
- Review, edge cases and docs: ~45 min
- Manual testing (running it by hand against the real service, broken and empty
  input files, `--name`, checking the README examples): ~30 min

## With more time
In priority order:

1. **Disambiguate registry hits.** 50 of 56 records are registered in a different
   country than the one investigated, so some are likely namesakes. Checking country
   and aliases before accepting a record would cut false follow-ups: the biggest
   accuracy gain left.
2. **Registry fallback on aliases**: when a name has no record, try its watchlist
   aliases (`PGPC` → "Persian Gulf Petrochemical").
3. **Name variants**: real screening needs transliteration variants (Mohammad /
   Mohammed). I'd add them as reviewed alias data rather than fuzzy matching, which
   the spec rules out and which floods analysts with false hits.
4. **Production hardening** if this became a long-lived service: a half-open circuit
   breaker, a shorter connect timeout, `Retry-After`, proxy support.
5. **Scale**: stream output for very large batches, and persist the media index
   between runs (the archive scan is now the slowest phase).
6. A golden end-to-end test of the full report, so CI doesn't depend on the
   20-second validator.
