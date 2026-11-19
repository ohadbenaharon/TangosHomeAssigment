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


def test_non_utf8_csv_is_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "i.csv"
    path.write_bytes(b"Entity name,Entity type,Country\nCaf\xe9 Co,organization,IR\n")
    with pytest.raises(InputError, match="cannot read"):
        load_investigations(path)


def test_name_without_letters_or_digits_is_an_input_error(tmp_path: Path) -> None:
    """Names compare by word tokens; '---' and '&&&' would silently merge into one."""
    path = tmp_path / "i.csv"
    path.write_text("Entity name,Entity type,Country\n---,organization,IR\n")
    with pytest.raises(InputError, match="no letters or digits"):
        load_investigations(path)


def test_corrupt_gzip_is_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl.gz"
    data = gzip.compress(b'{"id": "AM-1", "published": "", "title": "", "body": ""}\n' * 50)
    path.write_bytes(data[:20] + b"\x00" * 40 + data[60:])
    with pytest.raises(InputError):
        list(iter_articles(path))


@pytest.mark.parametrize(
    "content", ["", "Entity name,Entity type,Country\n", "Entity name,Entity type,Country\n\n\n"]
)
def test_investigations_file_without_rows_is_an_input_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "i.csv"
    path.write_text(content)
    with pytest.raises(InputError):
        load_investigations(path)


def test_watchlist_with_only_a_header_is_an_input_error(tmp_path: Path) -> None:
    """An empty watchlist would silently report every entity as clean."""
    path = tmp_path / "s.csv"
    path.write_text("Entity name,Aliases,Risk,Country of origin,List\n")
    with pytest.raises(InputError, match="no watchlist entries"):
        load_sanctions(path)


def test_media_archive_without_articles_is_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl.gz"
    with gzip.open(path, "wt") as fh:
        fh.write("\n")
    with pytest.raises(InputError, match="no articles"):
        list(iter_articles(path))


@pytest.mark.parametrize(
    ("row", "problem"),
    [("A Co,organization\n", "fewer"), ("A Co,organization,IR,extra\n", "more")],
)
def test_row_with_wrong_number_of_fields_is_an_input_error(
    tmp_path: Path, row: str, problem: str
) -> None:
    """A missing trailing field would otherwise silently become an empty country."""
    path = tmp_path / "i.csv"
    path.write_text("Entity name,Entity type,Country\n" + row)
    with pytest.raises(InputError, match=rf"i\.csv:2: .*{problem}"):
        load_investigations(path)


def test_bom_crlf_quoted_commas_and_padding_are_accepted(tmp_path: Path) -> None:
    path = tmp_path / "i.csv"
    path.write_bytes(
        b"\xef\xbb\xbfEntity name,Entity type,Country\r\n"
        b'"Aria Shipping Lines, Inc" , organization , ir \r\n'
    )
    [inv] = load_investigations(path)
    assert (inv.name, inv.entity_type, inv.country) == (
        "Aria Shipping Lines, Inc",
        "organization",
        "IR",
    )
