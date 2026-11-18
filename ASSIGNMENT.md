# Tangos — Backend (IC) Take-Home: Investigation Orchestrator

This document describes the home assignment, which is part of the Tangos
backend engineering recruitment process.

## General guidelines

1. The assignment is time-boxed to **4 hours**: go over the requirements,
   sketch a high-level design for your solution, and implement as much as
   you can within the box. How you allocate the time is up to you. We favor
   higher-quality code over completing more checkpoints.
2. While designing and implementing your solution, take extensibility,
   readability and testability into consideration. No need to make things
   over-generic.
3. **Python 3.12+ with complete type annotations is our main language and the
   expected choice.** Another OO language is acceptable if you are
   substantially more productive in it — but not TypeScript or C++.
4. We build with AI assistants and expect you to do the same — use whatever
   tools you normally work with. **You own every line you submit** and should
   be ready to explain and defend any of it.
5. Treat this document as a product spec. Where it is ambiguous or leaves
   room, make the call you would make in production — and record the
   decision, and why, in `NOTES.md`. We grade the quality of those calls,
   not whether you guessed what we had in mind.

## Assignment

Acme Bank's compliance team screens counterparties before doing business with
them. Screening an entity means investigating it across several data sources —
a sanctions watchlist, an adverse-media archive, and a corporate registry —
and producing a unified findings report. Your job is to build the
investigation orchestrator.

You are given:

- `data/sanctions.csv` — the watchlist. Columns:
  `Entity name,Aliases,Risk,Country of origin,List`
  (`Aliases` is `|`-separated and may be empty).
- `data/media.jsonl.gz` — adverse-media archive (~50MB uncompressed), one
  JSON object per line: `{"id": "AM-004512", "published": "2025-11-03",
  "title": "...", "body": "..."}`.
- `data/investigations.csv` — the entities to screen. Columns:
  `Entity name,Entity type,Country`.
- `registry_service.py` — a local corporate-registry HTTP service
  (stdlib-only; run with `python3 registry_service.py`, listens on
  `localhost:7311`). Its API is described in `API.md`. Lookups are slow —
  that is a property of the real upstream provider we mirror.

**Matching rule:** an entity matches a watchlist row or a media article when
its name or any alias appears as a whole phrase on word boundaries
(case-insensitive). `Aria Shipping` does **not** match an article that only
mentions `Aria Shipping Lines`.

## Checkpoints

1. Write a program that takes the watchlist, the media archive and the
   investigations file, and for each investigated entity reports its
   **distinct** findings (as in, don't report the same watchlist entry or
   media article multiple times): watchlist matches and the distinct media
   articles that mention it.

   Example — program output (default text format):

   ```
   Aria Shipping Lines:
     sanctions: OFAC (High, IR)
     media: 17 articles (AM-004512, AM-008133, ...)
   Northwind Logistics:
     no findings
   ```

   Your program must also support `--format json`, emitting one JSON object
   per investigated entity with at least the keys `entity`, `sanctions`
   (list of `{list, risk, country}`), and `media` (`{count, article_ids}`).
   Checkpoint 2 adds a `registry` key and checkpoint 4 a `follow_ups` key;
   their internal shape — and everything else about the schema — is up to
   you.

2. The compliance team found out that some investigated entities also have
   corporate-registry records with officers and linked companies. Integrate
   the registry service (see `API.md`) as a third data source and include
   the registry record reference and officer count in the report.
   Investigations repeat entities and aliases — **make sure to cache your
   lookups.**

   Example — report entry with registry findings:

   ```
   Aria Shipping Lines:
     sanctions: OFAC (High, IR)
     media: 17 articles (AM-004512, AM-008133, ...)
     registry: REG-IR-0042 (7 officers, 3 linked companies)
   ```

3. Add routing rules controlling which data sources run for which
   investigations:

   a. **Source include/exclude** — run only the named sources / never run
      the named sources (`sanctions`, `media`, `registry`).
   b. **Country include/exclude** — only investigate (or skip) entities
      whose `Country` is in the given list.
   c. **Entity-name include/exclude** — include or exclude investigations
      whose entity name matches a regular expression.

   The program must support multiple rules of potentially multiple types in
   the same run. For example: include only sources `sanctions` and
   `registry`, restricted to countries `IR,RU`, excluding entities matching
   `^Test`, while excluding country `US`. If no include rule of a given type
   is specified, everything is included for that type.

4. Investigators noticed that risk often hides one hop away — in the
   officers and linked companies sitting inside registry records. Extend the
   orchestrator to spawn **follow-up investigations** for them: screen each
   discovered officer and linked company against the watchlist and the media
   archive (follow-ups do not query the registry — depth is limited to 1).
   Do not investigate the same entity twice in a run. Then improve the
   run-time duration of the application by adding concurrency — a full
   default run should not take minutes.

   Example — report entry with follow-up findings:

   ```
   Aria Shipping Lines:
     sanctions: OFAC (High, IR)
     media: 17 articles (AM-004512, AM-008133, ...)
     registry: REG-IR-0042 (7 officers, 3 linked companies)
     follow-ups:
       Dariush Rahimi (officer): sanctions: EU (High, IR); media: 3 articles
       Caspian Trade Bank (linked): sanctions: OFAC (High, IR)
   ```

## Deliverables

- Your code, with run instructions in a README.
- Tests for the parts you consider worth testing.
- `NOTES.md` — a short document covering: your design and its trade-offs,
  **the decisions you made where the spec left room — and why**,
  **how you validated that your solution is correct**, the actual time you
  spent per checkpoint, anything that surprised you along the way, and what
  you would do with more time.
