from orchestrator.matching import PhraseIndex
from orchestrator.names import name_key
from orchestrator.sources.media import MediaIndex
from orchestrator.sources.sanctions import Watchlist
from tests.conftest import article, sanction


def test_identical_watchlist_rows_are_one_finding() -> None:
    wl = Watchlist([sanction("Caspian Trade Bank"), sanction("Caspian Trade Bank")])
    assert wl.screen(name_key("caspian trade bank")) == (sanction("Caspian Trade Bank"),)


def test_same_name_on_different_lists_are_distinct_findings() -> None:
    eu, ofac = sanction("X Corp", list_name="EU"), sanction("X Corp", list_name="OFAC")
    assert Watchlist([ofac, eu]).screen(name_key("X Corp")) == (eu, ofac)


def test_watchlist_matches_alias() -> None:
    entry = sanction("Persian Gulf Petrochemical", "PGPC")
    assert Watchlist([entry]).screen(name_key("pgpc")) == (entry,)


def test_watchlist_requires_whole_name_not_substring() -> None:
    wl = Watchlist([sanction("Aria Shipping Lines")])
    assert wl.screen(name_key("Aria Shipping")) == ()
    assert wl.screen(name_key("Aria Shipping Lines Ltd")) == ()


def test_aliases_of_returns_all_names_of_matched_rows() -> None:
    wl = Watchlist([sanction("Persian Gulf Petrochemical", "PGPC"), sanction("Other", "O")])
    assert wl.aliases_of(name_key("PGPC")) == {
        name_key("PGPC"),
        name_key("Persian Gulf Petrochemical"),
    }
    assert wl.aliases_of(name_key("Unlisted Co")) == {name_key("Unlisted Co")}


def test_media_counts_distinct_articles_across_title_body_and_aliases() -> None:
    names = PhraseIndex([name_key("Aria Shipping Lines"), name_key("ASL")])
    index = MediaIndex.scan(
        [
            article("AM-2", "ASL and Aria Shipping Lines", title="Aria Shipping Lines"),
            article("AM-1", "nothing here"),
            article("AM-3", "aria shipping lines"),
        ],
        names,
    )
    found = index.screen([name_key("Aria Shipping Lines"), name_key("ASL")])
    assert found.article_ids == ("AM-2", "AM-3")


def test_media_name_is_not_formed_across_title_body_seam() -> None:
    names = PhraseIndex([name_key("Aria Shipping Lines")])
    index = MediaIndex.scan([article("AM-1", "Lines were busy", title="Aria Shipping")], names)
    assert index.screen([name_key("Aria Shipping Lines")]).count == 0
