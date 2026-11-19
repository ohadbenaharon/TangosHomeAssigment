"""Rendering reports as human-readable text or JSON Lines."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from orchestrator.models import (
    EntityReport,
    Failed,
    FollowUp,
    FollowUpKind,
    MediaFindings,
    NotRun,
    Outcome,
    Ran,
    RegistryFindings,
    RegistryRecord,
    SanctionEntry,
    Screening,
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
    if not lines:
        lines.append(_no_findings(report))
    # As in the README example, only follow-ups with something to say are listed;
    # JSON carries all of them.
    if isinstance(report.follow_ups, Ran):
        flagged = [f for f in report.follow_ups.value if f.screening.has_findings]
        if flagged:
            lines.append("follow-ups:")
            lines.extend(f"  {_follow_up_text(f)}" for f in flagged)
    return f"{report.entity}:\n" + "".join(f"  {line}\n" for line in lines)


def _follow_up_text(follow_up: FollowUp) -> str:
    parts: list[str] = []
    match follow_up.screening.sanctions:
        case Ran(value=entries) if entries:
            parts.append(f"sanctions: {_sanctions_text(entries)}")
        case Failed(error=error):
            parts.append(f"sanctions: ERROR ({error})")
    match follow_up.screening.media:
        case Ran(value=media) if media.count:
            parts.append(f"media: {_plural(media.count, 'article')}")
        case Failed(error=error):
            parts.append(f"media: ERROR ({error})")
    return f"{follow_up.name} ({follow_up.kind}): {'; '.join(parts)}"


def _no_findings(report: EntityReport) -> str:
    # When routing switched sources off, say what "no findings" covers.
    checked = [s.value for s, o in report.outcomes.items() if isinstance(o, Ran)]
    if len(checked) == len(report.outcomes):
        return "no findings"
    return f"no findings (checked: {', '.join(checked)})"


def _sanctions_text(entries: Iterable[SanctionEntry]) -> str:
    # Comma-joined: "; " separates sources on a follow-up line.
    return ", ".join(f"{e.list_name} ({e.risk}, {e.country})" for e in entries)


def _media_text(media: MediaFindings) -> str:
    ids = ", ".join(media.article_ids[:MAX_IDS_IN_TEXT])
    more = ", ..." if media.count > MAX_IDS_IN_TEXT else ""
    return f"{_plural(media.count, 'article')} ({ids}{more})"


def _registry_text(record: RegistryRecord, investigated_country: str) -> str:
    officers = _plural(len(record.officers), "officer")
    linked = _plural(len(record.linked), "linked company", "linked companies")
    # Name-only lookups can hit a same-named company elsewhere; surface it.
    # Only a known, differing country is worth flagging; "" means the input had none.
    differs = investigated_country and record.country != investigated_country
    where = f"; registered in {record.country}" if differs else ""
    return f"{record.id} ({officers}, {linked}{where})"


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


# --- JSON Lines --------------------------------------------------------------------
# Each source key is null when the source did not produce a value (not run or
# failed); `source_status` says which, and `errors` carries failure messages.


def render_json(reports: Iterable[EntityReport]) -> str:
    return "".join(json.dumps(report_to_dict(r), ensure_ascii=False) + "\n" for r in reports)


def report_to_dict(report: EntityReport) -> dict[str, Any]:
    outcomes = report.outcomes
    errors = {s.value: o.error for s, o in outcomes.items() if isinstance(o, Failed)}
    return {
        "entity": report.entity,
        "entity_type": report.entity_type,
        "country": report.country,
        **_screening_dict(Screening(report.sanctions, report.media)),
        "registry": _value(report.registry, _registry_dict),
        "follow_ups": _value(report.follow_ups, lambda fs: [_follow_up_dict(f) for f in fs]),
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


def _follow_up_dict(follow_up: FollowUp) -> dict[str, Any]:
    relationship = "roles" if follow_up.kind is FollowUpKind.OFFICER else "relations"
    return {
        "name": follow_up.name,
        "kind": follow_up.kind.value,
        relationship: list(follow_up.relationship),
        **_screening_dict(follow_up.screening),
    }


def _screening_dict(screening: Screening) -> dict[str, Any]:
    return {
        "sanctions": _value(screening.sanctions, lambda es: [_sanction_dict(e) for e in es]),
        "media": _value(screening.media, _media_dict),
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
