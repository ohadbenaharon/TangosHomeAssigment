from orchestrator.matching import PhraseIndex
from orchestrator.names import name_key


def index(*names: str) -> PhraseIndex:
    return PhraseIndex(name_key(n) for n in names)


def found(idx: PhraseIndex, text: str) -> set[str]:
    return {" ".join(key) for key in idx.find(text)}


def test_name_key_is_case_whitespace_and_punctuation_insensitive() -> None:
    assert name_key("  Aria   SHIPPING-lines ") == ("aria", "shipping", "lines")


def test_matches_whole_phrase_case_insensitively() -> None:
    idx = index("Aria Shipping Lines")
    assert found(idx, "Yesterday ARIA shipping lines said...") == {"aria shipping lines"}


def test_spec_example_shorter_name_not_matched_inside_longer_mention() -> None:
    idx = index("Aria Shipping", "Aria Shipping Lines")
    assert found(idx, "Officials at Aria Shipping Lines declined.") == {"aria shipping lines"}


def test_shorter_name_matches_when_mentioned_on_its_own() -> None:
    idx = index("Aria Shipping", "Aria Shipping Lines")
    text = "Aria Shipping Lines and Aria Shipping are unrelated."
    assert found(idx, text) == {"aria shipping lines", "aria shipping"}


def test_no_match_on_partial_words() -> None:
    idx = index("Aria Shipping")
    assert found(idx, "Ariana Shipping and Aria Shippingco") == set()


def test_punctuation_is_a_word_boundary() -> None:
    idx = index("Caspian Trade Bank")
    assert found(idx, "(Caspian Trade Bank's) results") == {"caspian trade bank"}


def test_overlapping_but_not_nested_mentions_are_both_kept() -> None:
    idx = index("North Star", "Star Holdings")
    assert found(idx, "North Star Holdings") == {"north star", "star holdings"}


def test_name_at_end_of_text_and_prefix_of_unknown_longer_name() -> None:
    idx = index("Aria Shipping", "Aria Shipping Lines")
    assert found(idx, "We spoke to Aria Shipping") == {"aria shipping"}
    assert found(idx, "We spoke to Aria Shipping Line") == {"aria shipping"}


def test_empty_names_are_ignored() -> None:
    idx = index("", "---", "Zagros Freight")
    assert len(idx) == 1
    assert found(idx, "--- Zagros Freight") == {"zagros freight"}
