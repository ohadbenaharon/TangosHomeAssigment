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

### Output
- `--format json` is **JSON Lines**: one object per entity per line (literal reading
  of the spec; streamable; `jq`-friendly).
- A source that did not run is `null` (not `[]`); `source_status` says
  `ok` / `not_run` / `error` per source, `errors` carries messages.
- JSON adds `entity_type`, `country`, and `listed_name` per sanction (which watchlist
  entry matched — needed when the match was on an alias, e.g. `PGPC`).
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
- `Aria Shipping Lines` yields 17 articles, matching the README example.
- Performance: one archive pass takes ~1.1s vs ~37s for the naive per-entity regex.

## Surprises
- The README's own matching example contradicts plain `\b` regex semantics.
- `API.md` doesn't match `registry_service.py` (bulk endpoint always 404s, officers
  are paginated, `links` vs `linked_entities`) — handled in checkpoint 2.

## Time spent
- Checkpoint 1: _tbd_

## With more time
- _tbd_
