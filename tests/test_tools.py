"""The agent's tools: what a quote has to become before the board can anchor it."""

import sys
from pathlib import Path

import pytest

from board import app, tools


def test_visible_strips_the_markup_a_quote_carries_from_the_read():
    assert tools._visible("**Go:** a sentence") == "Go: a sentence"
    assert tools._visible("*emphasis* and `code`") == "emphasis and code"
    assert tools._visible("## Heading here") == "Heading here"
    assert tools._visible("- item one") == "item one"
    assert tools._visible("See [zrb](https://x.y) for more.") == "See zrb for more."
    assert (
        tools._visible("a sentence\nspanning two lines")
        == "a sentence spanning two lines"
    )


def test_visible_keeps_an_underscore_inside_a_word():
    assert tools._visible("call foo_bar(x)") == "call foo_bar(x)"
    assert tools._visible("_emphasis_ here") == "emphasis here"


def test_visible_drops_the_marker_a_note_leaves_between_lines():
    assert (
        tools._visible("a sentence ⟦a1 · go · block abc — a note⟧ and more")
        == "a sentence and more"
    )


def test_held_cuts_from_the_block_so_the_server_can_anchor_it():
    block = "Go: a sentence spanning two lines"
    quote = "**Go:** a sentence\nspanning two lines"
    assert tools._held(block, tools._visible(quote)) == block


def test_held_shortens_to_what_the_block_really_holds():
    assert tools._held("alpha beta", "alpha beta gamma delta") == "alpha beta"
    assert tools._held("nothing here", "alpha beta") is None


def test_block_holding_prefers_the_block_with_most_of_the_quote():
    blocks = [
        {"id": "long", "text": "one two three four five six"},
        {"id": "short", "text": "two three"},
        {"id": "part", "text": "one two three four"},
    ]
    # "two three four" is in `long` and `part`; the tighter of those wins.
    assert tools._block_holding(blocks, "two three four") == (blocks[2], "two three four")
    # A quote the block only partly holds anchors on the part it does.
    assert tools._block_holding(blocks, "two three four five six seven") == (
        blocks[0],
        "two three four five six",
    )


def test_block_holding_refuses_a_quote_with_no_words():
    with pytest.raises(RuntimeError, match="needs a quote"):
        tools._block_holding([{"id": "a", "text": "words"}], "   ")


def test_block_holding_says_what_the_document_holds():
    with pytest.raises(RuntimeError, match="no block holds"):
        tools._block_holding([{"id": "a", "text": "words"}], "elsewhere")


def test_ink_is_not_a_note():
    assert tools._is_note({"id": "a1", "kind": "note"})
    assert tools._is_note({"id": "a1"})  # an annotation older than `kind`
    ink = {"id": "a1", "kind": "ink", "strokes": [{}, {}]}
    assert not tools._is_note(ink)


def test_marks_fold_per_document_and_keep_the_newest_time():
    inbox = [
        "- 2026-11-08T09:02:26+07:00 — board: 0001-one",
        "- 2026-11-08T09:04:34+07:00 — board: 0002-two",
        "- 2026-11-08T12:44:56+07:00 — board: 0001-one",
    ]
    assert tools._marks_waiting(inbox) == [
        "- 0001-one ×2 — newest 2026-11-08T12:44:56+07:00",
        "- 0002-two — 2026-11-08T09:04:34+07:00",
    ]
    assert tools._marks_waiting([]) == ["(no marks waiting)"]


def test_init_path_names_the_file_shipped_beside_the_server(monkeypatch, capsys):
    # `zrb-board init-path` is what a shell puts in ZRB_INIT_SCRIPTS; it must name a real
    # file, or zrb warns about an init source that is not there.
    monkeypatch.setattr(sys, "argv", ["zrb-board", "init-path"])
    app.main()
    printed = Path(capsys.readouterr().out.strip())
    assert printed.is_file() and printed.name == "zrb_init.py"
