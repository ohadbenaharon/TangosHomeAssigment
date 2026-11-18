from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from orchestrator.models import Article, SanctionEntry


def sanction(
    name: str, *aliases: str, list_name: str = "OFAC", risk: str = "High", country: str = "IR"
) -> SanctionEntry:
    return SanctionEntry(
        list_name=list_name, risk=risk, country=country, name=name, aliases=aliases
    )


def article(id: str, body: str, title: str = "") -> Article:
    return Article(id=id, published="2025-01-01", title=title, body=body)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A miniature but complete set of input files."""
    (tmp_path / "investigations.csv").write_text(
        "Entity name,Entity type,Country\n"
        "Aria Shipping Lines,organization,IR\n"
        "Northwind Logistics,organization,us\n"
        "aria shipping lines,organization,IR\n"
        "PGPC,organization,IR\n"
    )
    (tmp_path / "sanctions.csv").write_text(
        "Entity name,Aliases,Risk,Country of origin,List\n"
        "Aria Shipping Lines,ASL|Aria Lines,High,IR,OFAC\n"
        "Aria Shipping Lines,ASL|Aria Lines,High,IR,OFAC\n"
        "Persian Gulf Petrochemical,PGPC,High,IR,EU\n"
    )
    rows = [
        {"id": "AM-1", "published": "2025-01-01", "title": "t", "body": "Aria Shipping Lines x"},
        {"id": "AM-2", "published": "2025-01-01", "title": "ASL news", "body": "ASL again"},
        {
            "id": "AM-3",
            "published": "2025-01-01",
            "title": "t",
            "body": "Persian Gulf Petrochemical",
        },
        {"id": "AM-4", "published": "2025-01-01", "title": "t", "body": "Northwind Logistic"},
    ]
    with gzip.open(tmp_path / "media.jsonl.gz", "wt") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
    return tmp_path
