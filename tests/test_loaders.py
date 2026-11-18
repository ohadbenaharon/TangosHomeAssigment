import gzip
from pathlib import Path

import pytest

from orchestrator.loaders import InputError, iter_articles, load_investigations, load_sanctions


def test_load_sanctions_splits_and_trims_aliases(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    path.write_text(
        "Entity name,Aliases,Risk,Country of origin,List\nA Co, x | |y ,High,ir,EU\n"
        "B Co,,Low,TR,UK\n"
    )
    a, b = load_sanctions(path)
    assert a.aliases == ("x", "y") and a.country == "IR"
    assert b.aliases == ()


def test_load_investigations_skips_blank_lines_and_uppercases_country(tmp_path: Path) -> None:
    path = tmp_path / "i.csv"
    path.write_text("Entity name,Entity type,Country\nA Co,organization,ir\n\n")
    [inv] = load_investigations(path)
    assert inv.country == "IR"


def test_missing_column_is_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "i.csv"
    path.write_text("Name,Country\nA,IR\n")
    with pytest.raises(InputError, match="missing column"):
        load_investigations(path)


def test_empty_entity_name_is_an_input_error_with_line_number(tmp_path: Path) -> None:
    path = tmp_path / "i.csv"
    path.write_text("Entity name,Entity type,Country\nA,organization,IR\n,organization,IR\n")
    with pytest.raises(InputError, match=r"i\.csv:3"):
        load_investigations(path)


def test_malformed_article_line_is_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl.gz"
    with gzip.open(path, "wt") as fh:
        fh.write('{"id": "AM-1", "published": "", "title": "", "body": ""}\n{"id": \n')
    with pytest.raises(InputError, match=r"m\.jsonl\.gz:2"):
        list(iter_articles(path))


def test_missing_file_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        list(iter_articles(tmp_path / "nope.gz"))
    with pytest.raises(InputError):
        load_sanctions(tmp_path / "nope.csv")
