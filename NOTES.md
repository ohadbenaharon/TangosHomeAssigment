# NOTES

## Design

A phased pipeline (`orchestrator/orchestrator.py`). The phases are explicit because
the media scan needs every name up front, and follow-up names only exist once the
registry has answered:

1. **Route & de-duplicate** — drop rows the rules exclude, merge repeated entities.
2. **Registry fan-out** — concurrent lookups through a thread pool (I/O bound).
3. **Collect follow-ups** — officers and linked companies of the records found.
4. **Screen every distinct name once** — watchlist lookup + **one** pass over the
   media archive for all names (primaries, follow-ups, aliases) together.
5. **Assemble** reports in input order, render as text or JSON Lines.

Modules:
- `names.py` — the one definition of "same name": NFKC + casefold + word tokens.
  Watchlist matching, media matching, the registry cache and follow-up de-dup all
  use it, so the rule can't drift between sources.
- `matching.py` — `PhraseIndex`, a dictionary matcher over token n-grams (linear in
  text length, not names × text).
- `sources/` — `sanctions.py`, `media.py`, `registry.py` (HTTP client + cache).
- `routing.py` — `IncludeExclude[T]`, one implementation of include/exclude semantics
  shared by the source, country and name dimensions.
- `models.py` — immutable domain types. Every source result is
  `Ran[T] | NotRun | Failed`, so "checked, nothing found", "not checked" and "check
  failed" can never be conflated — the property I care most about in a screening tool.
- `cli.py` — the only module that knows about argv, stdout and exit codes.

Stdlib only at runtime. Dev: pytest, mypy `--strict`, ruff.

**Trade-offs.** Threads, not asyncio: the only I/O is one HTTP service, a thread pool
keeps every component synchronous and trivially testable, and the GIL is irrelevant
while threads wait on sockets. The media scan is single-threaded CPU work (~1.1s);
parallelising it (processes) would cost more complexity than it saves. Phases run
sequentially rather than overlapping the media scan with registry I/O, because the
scan must know the follow-up names — overlapping would mean two scans or a
speculative index; not worth it at a 3.6s total.

## Decisions where the spec left room

### Matching
- **Word boundaries alone don't implement the spec's rule.** `\bAria Shipping\b`
  matches inside "Aria Shipping Lines" (the space is a boundary), yet the README says
  it must not. Rule implemented: *a mention is discarded when it lies strictly inside
  a mention of another known name*; overlapping-but-not-nested mentions both count.
  Known names = investigated names, follow-ups, all watchlist names and aliases.
  Limitation: a longer name the system has never heard of can't shadow a shorter one
  — inherent to dictionary matching.
- **Watchlist matching is equality on the whole name/alias, not substring.** A
  watchlist field *is* a complete name; substring matching would flag
  "Aria Shipping" against a listing for "Aria Shipping Lines".
- **Punctuation is a word boundary** (`Caspian Trade Bank's`, `Aria-Shipping Lines`
  match). Title and body are matched separately so a name can't form across the seam.
- **Media search includes the entity's watchlist aliases** (one hop): `PGPC` also
  finds "Persian Gulf Petrochemical"; "Hormuz Textile Partners" finds "Hormuz TEX".
  The watchlist serves as an alias directory even when the sanctions source is routed
  off. Aliases-of-aliases are not chased, to avoid chaining unrelated entities.

### De-duplication
- **Identical watchlist rows are one finding** (`Caspian Trade Bank` is listed twice
  identically). The same name on two different lists stays two findings.
- **Investigations are de-duplicated by normalised name** (53 of 150 rows are exact
  duplicates → 97 entities); first occurrence wins, output keeps input order.
- **"Never investigate the same entity twice"** is enforced by keying screening on
  the normalised name across the whole run: 238 distinct names are screened once each;
  242 follow-up entries reference them (officers on several boards, and 5 follow-ups
  that are also primary investigations, share one result).

### Registry
- **Built against the service's actual behaviour, not `API.md`** — probed first:
  - `POST /entities/bulk` always returns 404, byte-identical to "not found". Using it
    would silently mark every entity as having no record → not used.
  - Officers are **paginated** (5 per page, `next_page` token sent back as `?page=`);
    records have up to 7. All pages are fetched, guarded against repeated tokens, a
    page cap, and the record id changing mid-pagination.
  - Links arrive as `links: [{name, relation}]`, repeated on every page → merged.
- **Officer count = distinct people.** 12 records list one person twice with different
  roles (e.g. `Emre Aydin`, director + secretary): one officer, both roles.
- **A failed lookup is `error`, never "no record".** Retries with exponential backoff
  (3 attempts) on network errors, timeouts, 429 and 5xx; other 4xx and malformed bodies
  fail immediately; 5s timeout per request. The report is still printed, affected
  entities show `registry: ERROR (...)`, follow-ups for them are marked unknown, and
  the process exits 1.
- **Cache** keyed by normalised name, holding *futures*, so concurrent lookups of one
  name share one in-flight request. Failures are cached for the run.
- **Registry country ≠ investigated country is surfaced, not rejected.** Lookups are by
  name only, so a hit can be a same-named company elsewhere. 50 of the 56 records
  found differ. Rejecting would hide officers/links that may matter, so the analyst
  decides: JSON carries the registry `country`; text appends `; registered in XX`.
  That is noisy here but deliberate — a possible identity mismatch is exactly what a
  reader of the text report shouldn't have to dig into JSON to notice.

### Routing
- **One semantics for every dimension**: includes OR'd, excludes OR'd, **exclude beats
  include**, no include rule ⇒ everything included; dimensions AND'd. Exclude-wins is
  the conservative reading: an explicit "never" isn't undone by a broader "only".
- **Rules judge each input row before de-duplication.**
- **Name regex: `re.search`, case-sensitive, on the name as written.** The user owns
  anchoring (`^Test`) and case (`(?i)`); silently anchoring or folding case would make
  some patterns inexpressible.
- **Country and name rules select primary investigations; source rules apply to every
  check, follow-ups included.** Follow-ups have no country, so country rules couldn't
  apply to them consistently; treating both entity rules alike keeps it predictable.
- **CLI**: flags repeat; source/country values comma-separated; regexes are not split
  on commas (`{1,2}` is valid). Unknown source, invalid regex, or rules leaving no
  source enabled → usage error (exit 2) before any work.
- **"no findings" is qualified when sources were routed off** —
  `no findings (checked: sanctions, registry)` — rather than overstating coverage.

### Follow-ups
- Officers and linked companies of every record found are screened against the
  watchlist and media (with their watchlist aliases), never the registry (depth 1).
- A record never follows up on itself.
- If the registry wasn't run, follow-ups are `null`/not run; if it failed, they're
  marked failed ("unknown"), not empty.
- Text lists only follow-ups with findings, as in the README example; JSON lists all.

### Output
- `--format json` is **JSON Lines** (one object per entity per line).
- `null` = no value (source not run, or failed); `source_status` says which per
  source, `errors` holds messages. `registry` is `{"found": false}` for "checked, no
  record". Extra keys: `entity_type`, `country`, `listed_name` per sanction (which
  entry matched — needed for alias hits like `PGPC`), officers/links with roles.
- Text shows at most 3 article ids; ids are always sorted → deterministic output.
- Exit codes: 0 ok, 1 report printed but some checks failed, 2 bad input/arguments.

### Input handling
- Malformed input fails loudly with file:line (exit 2): silently skipping a bad
  watchlist row or article would silently miss a hit.

## Concurrency and performance

| run | wall time |
|---|---|
| checkpoint 2 (sequential registry) | 34s |
| **full default run (16 workers)** | **3.6s** — registry 2.5s, media scan 1.1s |
| README routing example | 0.7s |
| registry service down | 5.5s (was 61s), all entities reported as errors |

The registry phase is bounded by the service's ~300ms per request: 108 requests
(97 lookups + 11 second officer pages); pages of one record are sequential, so with
16 in flight that's ~7–8 rounds × 0.3s ≈ 2.5s measured.
16 workers by default (`--registry-workers`): fast, and modest load on an upstream
with an SLA. Getting there needed two transport fixes, both measured:

- **urllib's macOS proxy lookup burned ~10s of CPU per run.** `urlopen` consults the
  system proxy configuration on every request; under 16 threads the run used ~14s of
  CPU for 4.9s of wall time. Switched to `http.client` (no proxy handling — fine for
  an internal service, noted below): 0.1s of CPU for the same requests.
- **The service's listen backlog is 5.** It closes every connection after one response
  (HTTP/1.0), so workers finishing together reconnect in bursts that overflow the
  backlog and get `ECONNRESET`. (The slow proxy lookup had been accidentally spacing
  connections out, which hid this.) Measured with 96 lookups: 5 workers → 0 resets but
  7s; 16 workers → 8–9 resets per run (retries absorb them, but each costs 0.2s and
  a burst of three could surface as a false registry error); allowing 4 concurrent
  `connect()`s didn't help (connect returns before the server `accept`s). **Opening
  connections one at a time** (a lock around `connect()` only; requests still overlap)
  → 0 resets at 16 and 32 workers, same 2.5s: a localhost connect takes microseconds,
  a response 300ms. At 64 workers resets reappear — another reason not to go higher.

## Validation

- **70 tests** (0.5s): matcher (incl. the README's Aria rule), sources, loaders,
  registry client (in-process fake server with failure injection and request counting,
  plus a **contract test against the provided `registry_service.Handler`**), cache
  (shared in-flight requests across threads), routing (semantics table + the README's
  combined example end to end), follow-ups (screen-once, reuse, depth 1, routing),
  concurrency (peak in-flight, ordering), and CLI output in both formats.
- **`scripts/validate_report.py`** re-derives an entire default run independently —
  raw `registry.json` instead of HTTP, csv watchlist, per-name regexes with its own
  span-containment implementation of the nested-name rule — and diffs every field of
  every entity and follow-up. Result: **97 entities, 242 follow-up entries, 0
  mismatches.** To make sure it isn't vacuous I planted three bugs in a copy of the
  report (naive-regex Aria count, unpaginated officer count, a dropped follow-up
  sanction); it flagged all three.
- Earlier, a plain `\b` regex over every entity differed from ours on exactly one
  entity: `Aria Shipping` (naive 21, ours 4) — all 17 extra articles mention
  `Aria Shipping Lines`; I read the remaining 4 by hand, genuine standalone mentions.
- README examples reproduced on real data: `Aria Shipping Lines` → OFAC (High, IR),
  17 articles, `REG-IR-0042 (7 officers, 3 linked companies)`, follow-ups
  `Dariush Rahimi (officer): sanctions: EU (High, IR); media: 3 articles` and
  `Caspian Trade Bank (linked): sanctions: OFAC (High, IR)`.
- Three consecutive full runs: byte-identical output, 0 retries.

## Surprises
- The README's own matching example contradicts plain `\b` regex semantics.
- `API.md` doesn't describe the service: bulk endpoint 404s, officers are paginated,
  `links` not `linked_entities`, no `aliases` in responses.
- A CPU-heavy urllib proxy lookup was masking a connection-reset problem; fixing the
  first exposed the second.

## Time spent
- Checkpoint 1: _tbd_
- Checkpoint 2: _tbd_
- Checkpoint 3: _tbd_
- Checkpoint 4: _tbd_

## With more time
- **Circuit breaker** for the registry: with the service down every lookup still
  spends its retries (5.5s total now, but proportional to entity count).
- **Proxy support** if the registry ever sits behind one (`http.client` ignores proxy
  settings by design here).
- **Registry fallback on aliases**: if a name has no record, retry with its watchlist
  aliases (`PGPC` → "Persian Gulf Petrochemical").
- **Streaming output** for very large batches, and media index persistence across
  runs (the archive scan dominates once the registry is fast).
- A golden end-to-end test pinning the full default report (today that role is played
  by `validate_report.py`, which needs the real data and ~20s).
