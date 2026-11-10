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


def test_visible_drops_an_image_the_page_never_shows():
    # The renderer loses the <img> tag and the alt text is not text on the page, so the
    # block holds "Before after"; keeping the alt would anchor on "Before" alone.
    assert tools._visible("Before ![diagram](d.png) after") == "Before after"
    assert tools._block_holding(
        [{"id": "a", "text": "Before after"}], "Before ![diagram](d.png) after"
    ) == ({"id": "a", "text": "Before after"}, "Before after")


def test_visible_shows_an_escape_as_the_character_itself():
    # `\*` is `*` on the page and is not emphasis: keeping the backslash matches no block,
    # and stripping it as markup would turn the escape into the emphasis it escaped.
    assert tools._visible("literal \\*asterisk\\*") == "literal *asterisk*"
    assert tools._visible("a \\_name\\_ here") == "a _name_ here"


def test_visible_reads_a_table_row_as_the_page_lays_it_out():
    # plain_text renders a row as its cells -- `| foo | bar |` is "foo bar" on the page --
    # and the rule under the header is nowhere at all.
    assert tools._visible("| Name | Value |") == "Name Value"
    assert tools._visible("| --- | --- |") == ""
    assert tools._visible("| foo | bar |") == "foo bar"
    # A pipe that is punctuation, not a cell border, stays where it is.
    assert tools._visible("either | or") == "either | or"
    assert tools._block_holding(
        [{"id": "t", "text": "Name Value foo bar"}], "| foo | bar |"
    ) == ({"id": "t", "text": "Name Value foo bar"}, "foo bar")


def test_visible_unescapes_the_entities_the_page_shows():
    # plain_text unescapes after stripping tags, so a block holds `Fish & Chips` while the
    # source says `Fish &amp; Chips`.
    assert tools._visible("Fish &amp; Chips") == "Fish & Chips"
    assert tools._block_holding(
        [{"id": "a", "text": "Fish & Chips"}], "Fish &amp; Chips"
    ) == ({"id": "a", "text": "Fish & Chips"}, "Fish & Chips")


def test_the_default_address_follows_the_board_settings(monkeypatch):
    # The tools call the port the server binds, not one of their own.
    monkeypatch.setenv("ZRB_BOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("ZRB_BOARD_PORT", "9000")
    assert tools._default_url() == "http://127.0.0.1:9000"
    monkeypatch.setenv("ZRB_BOARD_HOST", "::1")
    assert tools._default_url() == "http://[::1]:9000"
    monkeypatch.delenv("ZRB_BOARD_HOST")
    monkeypatch.delenv("ZRB_BOARD_PORT")
    assert tools._default_url() == "http://127.0.0.1:8765"


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


def test_block_holding_refuses_a_quote_no_block_holds_whole():
    # Not shortened to "two three four": a quote from a read the document has moved past
    # would otherwise land on whichever paragraph opens with the same words. The error
    # offers the block holding the most of it, so the caller can quote that instead.
    blocks = [
        {"id": "long", "text": "one two three four five six"},
        {"id": "short", "text": "two"},
    ]
    with pytest.raises(RuntimeError, match="two three four five six"):
        tools._block_holding(blocks, "two three four five six seven")


def test_the_read_view_is_told_apart_from_the_document():
    view = (
        "# Q\n"
        "rev 8173f5fe4d34 · updated 2026-11-10T07:26 · 1 annotation(s)\n"
        "source: docs/0001-question.md\n\n# Q\n\ntext\n"
    )
    assert tools._is_the_read_view(view)
    assert not tools._is_the_read_view("# Q\n\ntext\n")
    # A document may quote a digest of its own further down, and that is its business:
    # only the start of the text is read.
    quoted = "# Example\n\nA view looks like this:\n\n" + view
    assert not tools._is_the_read_view(quoted)


def test_the_source_view_is_the_document_itself(monkeypatch):
    # Nothing wrapped around it: what BoardRead(source=True) returns is byte for byte what
    # BoardWrite takes, so a caller cannot paste a wrapper back into the file.
    markdown = "# Q\n\ntext\n"
    monkeypatch.setattr(
        tools, "_json", lambda *args, **kwargs: {"rev": "abc", "markdown": markdown}
    )
    assert tools.BoardRead("0001-question", source=True) == markdown


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
