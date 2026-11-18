# NOTES

> Working draft — sections are filled in per checkpoint.

## Design

Phased pipeline (`orchestrator/orchestrator.py`): de-duplicate investigations →
resolve each entity's identity (its name + watchlist aliases) → scan the media
archive **once** for all names → assemble reports → render.

- `names.py` — the one definition of "same name": NFKC + casefold + word tokens.
  Every comparison in the system (watchlist, media, later registry cache and
  follow-up de-dup) uses it, so the matching rule can't drift between sources.
- `matching.py` — `PhraseIndex`: dictionary matcher over token n-grams.
- `sources/` — one module per data source; `models.py` — immutable domain types.
- Per-source results are `Ran[T] | NotRun | Failed`, so "checked, nothing found",
  "not checked" and "check failed" are never conflated.
- Stdlib only at runtime; dev tooling: pytest, mypy `--strict`, ruff.

## Decisions where the spec left room

### Matching
- **Word boundaries alone don't implement the spec's rule.** `\bAria Shipping\b`
  matches inside "Aria Shipping Lines" (the space is a boundary), yet the README
  says it must not. Rule implemented: *a mention of a name is discarded when it
  lies strictly inside a mention of another known name.* Overlapping-but-not-nested
  mentions both count. Known names = investigated names + all watchlist names and
  aliases (+ registry follow-ups later). Limitation: a longer name the system has
  never heard of can't shadow a shorter one — inherent to dictionary matching.
- **Watchlist matching is equality on the whole name/alias, not substring.** A
  watchlist field *is* a complete name; substring matching would flag
  "Aria Shipping" against a listing for "Aria Shipping Lines".
- **Tokenisation treats punctuation as a boundary**, so `Caspian Trade Bank's` and
  `Aria-Shipping Lines` match. Title and body are matched separately so a name
  can't be formed across the seam.
- **Media search uses the entity's watchlist aliases** (one hop): `PGPC` also finds
  articles naming "Persian Gulf Petrochemical"; "Hormuz Textile Partners" also
  finds "Hormuz TEX". The watchlist acts as an alias directory even if the
  sanctions source is routed off. Aliases-of-aliases are not chased, to avoid
  chaining unrelated entities.

### De-duplication
- **Identical watchlist rows are one finding** (`Caspian Trade Bank` is listed twice
  identically). The same name on two different lists stays two findings.
- **Investigations are de-duplicated by normalised name** (53 of 150 rows are exact
  duplicates); first occurrence wins, output keeps input order.

### Input handling
- Malformed input fails loudly with file:line (exit code 2). Silently skipping a bad
  watchlist row or article would silently miss a hit.
- Country codes are upper-cased; aliases are trimmed and empty ones dropped.

### Registry (checkpoint 2)
- **Built against the service's actual behaviour, not `API.md`** — I probed it before
  writing the client:
  - `POST /entities/bulk` always returns 404, which is byte-identical to "not found".
    Using it would silently mark every entity as having no record → not used.
  - Officers are **paginated** (5 per page, `next_page` token passed back as `?page=`);
    records have up to 7 officers. All pages are fetched; pagination is guarded
    against repeated tokens, a page cap, and the record id changing mid-way.
  - Links come as `links: [{name, relation}]` (not `linked_entities: [str]`) and are
    repeated on every page → merged, not concatenated.
- **Officer count = distinct people.** 12 records list one person twice with different
  roles (e.g. `Emre Aydin`, director + secretary in REG-RU-0116): one officer with
  roles `[director, secretary]`.
- **A failed lookup is `error`, never "no record".** Retries with exponential backoff
  (3 attempts, 0.2s → 0.4s) on connection errors, timeouts, 429 and 5xx; other 4xx and
  malformed bodies fail immediately. Explicit 5s per-request timeout. Any failed check
  → report still printed, entity shows `registry: ERROR (...)`, exit code 1.
- **Cache** (`CachingRegistry`) keyed by normalised name, holding *futures*: concurrent
  lookups of one name share a single in-flight request (ready for checkpoint 4).
  Failures are cached for the run so a dead name isn't hammered. The cache is separate
  from the HTTP client so either can be tested or replaced alone.
- **Registry country ≠ investigated country is surfaced, not rejected.** Lookups are by
  name only, so a hit can be a same-named company elsewhere. For 50 of the 56 distinct
  entities found in the registry the countries differ; text output appends `registered in XX`, JSON
  carries the registry `country`. Rejecting would hide officers/links that may matter;
  the analyst decides.
- Query is sent with the investigation's original spelling; cache key is normalised.
  (The service matches on `casefold().strip()`, a slightly narrower equivalence than
  ours — irrelevant for this data, noted for production.)

### Output
- `--format json` is **JSON Lines**: one object per entity per line (literal reading
  of the spec; streamable; `jq`-friendly).
- A source that did not run is `null` (not `[]`); `source_status` says
  `ok` / `not_run` / `error` per source, `errors` carries messages.
- JSON adds `entity_type`, `country`, and `listed_name` per sanction (which watchlist
  entry matched — needed when the match was on an alias, e.g. `PGPC`).
- JSON `registry`: `{"found": false}` for "checked, no record" vs `null` for
  not-run/failed; when found: id, name, country, counts, officers (with roles) and
  linked companies (with relations).
- Text output lists at most 3 article ids, sorted. Article ids are always sorted so
  output is deterministic.

## Validation
- Unit tests for the matcher (incl. the spec's Aria example), sources, loaders,
  orchestrator, and CLI output (text + JSON).
- `scripts/crosscheck_media.py` re-derives every entity's media hits with a naive
  brute-force `\b...\b` regex over all 120k articles and diffs against ours.
  Result: **97 entities, 1 difference** — `Aria Shipping` (ours 4, naive 21), where
  all 17 extra articles mention `Aria Shipping Lines`. I read the 4 remaining
  contexts by hand; they are genuine standalone mentions.
- `Aria Shipping Lines` yields 17 articles, matching the README example, and
  `REG-IR-0042 (7 officers, 3 linked companies)` — 7 only because pagination is followed.
- Registry results for all 97 entities were cross-checked against `data/registry.json`
  directly: 56 found, 41 not found, ids / distinct-officer / link counts all match.
- Registry client is tested against an in-process fake (failure injection, request
  counting) **and** a contract test against the provided `registry_service.Handler`
  (latency patched to 0), to catch drift between client and service.
- Killed the service mid-way: every entity reports a registry ERROR, exit code 1.
- Performance: one archive pass takes ~1.1s vs ~37s for the naive per-entity regex.

## Surprises
- The README's own matching example contradicts plain `\b` regex semantics.
- `API.md` doesn't match `registry_service.py` (bulk endpoint always 404s, officers
  are paginated, `links` vs `linked_entities`, no `aliases` in responses).

## Time spent
- Checkpoint 1: _tbd_

## With more time
- Circuit breaker for the registry: with the service down, every lookup still burns
  its retries (~60s sequentially for 97 entities) before reporting errors.
- HTTP keep-alive / connection pooling (urllib opens a connection per request).
