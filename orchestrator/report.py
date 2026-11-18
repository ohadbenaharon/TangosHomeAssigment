"""Rendering reports as human-readable text or JSON Lines."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from orchestrator.models import (
    EntityReport,
    Failed,
    MediaFindings,
    NotRun,
    Outcome,
    Ran,
    RegistryFindings,
    RegistryRecord,
    SanctionEntry,
    Source,
)

MAX_IDS_IN_TEXT = 3


# --- text ------------------------------------------------------------------------


def render_text(reports: Iterable[EntityReport]) -> str:
    return "".join(_text_entry(r) for r in reports)


def _text_entry(report: EntityReport) -> str:
    lines: list[str] = []
    match report.sanctions:
        case Ran(value=entries) if entries:
            lines.append(f"sanctions: {_sanctions_text(entries)}")
        case Failed(error=error):
            lines.append(f"sanctions: ERROR ({error})")
    match report.media:
        case Ran(value=media) if media.count:
            lines.append(f"media: {_media_text(media)}")
        case Failed(error=error):
            lines.append(f"media: ERROR ({error})")
    match report.registry:
        case Ran(value=RegistryFindings(record=record)) if record is not None:
            lines.append(f"registry: {_registry_text(record, report.country)}")
        case Failed(error=error):
            lines.append(f"registry: ERROR ({error})")
    body = "".join(f"  {line}\n" for line in lines) if lines else "  no findings\n"
    return f"{report.entity}:\n{body}"


def _sanctions_text(entries: Iterable[SanctionEntry]) -> str:
    return "; ".join(f"{e.list_name} ({e.risk}, {e.country})" for e in entries)


def _media_text(media: MediaFindings) -> str:
    ids = ", ".join(media.article_ids[:MAX_IDS_IN_TEXT])
    more = ", ..." if media.count > MAX_IDS_IN_TEXT else ""
    return f"{_plural(media.count, 'article')} ({ids}{more})"


def _registry_text(record: RegistryRecord, investigated_country: str) -> str:
    officers = _plural(len(record.officers), "officer")
    linked = _plural(len(record.linked), "linked company", "linked companies")
    # Name-only lookups can hit a same-named company elsewhere; surface it.
    where = f"; registered in {record.country}" if record.country != investigated_country else ""
    return f"{record.id} ({officers}, {linked}{where})"


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


# --- JSON Lines --------------------------------------------------------------------
# Each source key is null when the source did not produce a value (not run or
# failed); `source_status` says which, and `errors` carries failure messages.


def render_json(reports: Iterable[EntityReport]) -> str:
    return "".join(json.dumps(report_to_dict(r), ensure_ascii=False) + "\n" for r in reports)


def report_to_dict(report: EntityReport) -> dict[str, Any]:
    outcomes: dict[Source, Outcome[Any]] = {
        Source.SANCTIONS: report.sanctions,
        Source.MEDIA: report.media,
        Source.REGISTRY: report.registry,
    }
    errors = {s.value: o.error for s, o in outcomes.items() if isinstance(o, Failed)}
    return {
        "entity": report.entity,
        "entity_type": report.entity_type,
        "country": report.country,
        "sanctions": _value(report.sanctions, lambda es: [_sanction_dict(e) for e in es]),
        "media": _value(report.media, _media_dict),
        "registry": _value(report.registry, _registry_dict),
        "source_status": {s.value: _status(o) for s, o in outcomes.items()},
        **({"errors": errors} if errors else {}),
    }


def _value[T](outcome: Outcome[T], convert: Callable[[T], Any]) -> Any:
    return convert(outcome.value) if isinstance(outcome, Ran) else None


def _status(outcome: Outcome[Any]) -> str:
    match outcome:
        case Ran():
            return "ok"
        case NotRun():
            return "not_run"
        case Failed():
            return "error"


def _sanction_dict(entry: SanctionEntry) -> dict[str, Any]:
    return {
        "list": entry.list_name,
        "risk": entry.risk,
        "country": entry.country,
        "listed_name": entry.name,
    }


def _media_dict(media: MediaFindings) -> dict[str, Any]:
    return {"count": media.count, "article_ids": list(media.article_ids)}


def _registry_dict(findings: RegistryFindings) -> dict[str, Any]:
    record = findings.record
    if record is None:
        return {"found": False}
    return {
        "found": True,
        "id": record.id,
        "name": record.name,
        "country": record.country,
        "officer_count": len(record.officers),
        "linked_count": len(record.linked),
        "officers": [{"name": o.name, "roles": list(o.roles)} for o in record.officers],
        "linked": [{"name": c.name, "relations": list(c.relations)} for c in record.linked],
    }
