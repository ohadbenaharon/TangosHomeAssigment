# Acme Corporate Registry — API Reference

Base URL: `http://localhost:7311` — start the service with
`python3 registry_service.py` (Python 3.12+, no dependencies).

> **SLA note:** the upstream provider we mirror enforces ~300ms response
> latency per request. Plan accordingly.

## GET /entities?name=<name>

Look up a corporate-registry record by entity name. Matching is exact on
the registered name or any registered alias, case-insensitive.

Response `200`:

```json
{
  "id": "REG-IR-0042",
  "name": "Aria Shipping Lines",
  "country": "IR",
  "officers": [{"name": "Farhad Noori", "role": "director"}],
  "linked_entities": ["Caspian Trade Bank", "Zagros Freight"]
}
```

- `officers` — the full list of registered officers.
- `linked_entities` — names of related companies (subsidiaries,
  shareholders, parent companies).

Response `404`: `{"error": "not found"}` — the entity has no registry
record.

## POST /entities/bulk

Recommended for batch screening. Accepts `{"names": ["..."]}` and returns
one record (or `null`) per name in a single round-trip, avoiding the
per-request latency.
