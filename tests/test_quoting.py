"""Where a quote lands: the file's markdown rendered where it stands, not on its own."""

from board import quoting, render

TABLE = "# Q\n\n| Name | Value |\n| --- | --- |\n| foo | bar |\n"


def test_a_table_row_is_a_row_only_under_its_header():
    # Rendered alone, `| foo | bar |` is a paragraph; the sentinels put it back inside its
    # table, which is the whole reason this is not a normalisation the tools could do.
    block, words = quoting.locate(TABLE, "| foo | bar |")
    assert words == "foo bar"
    assert block == next(b.id for b in render.render(TABLE).blocks if "foo bar" in b.text)


def test_a_quote_carries_its_markdown_and_still_lands():
    for markdown, quote, words in (
        ("# Q\n\n**Go:** a sentence\n", "**Go:** a sentence", "Go: a sentence"),
        ("# Q\n\nSee [zrb](https://x.y).\n", "See [zrb](https://x.y).", "See zrb ."),
        ("# Q\n\nliteral \\*asterisk\\*\n", "literal \\*asterisk\\*", "literal *asterisk*"),
        ("# Q\n\nFish &amp; Chips\n", "Fish &amp; Chips", "Fish & Chips"),
        (
            "# Q\n\nBefore ![d](d.png) after\n",
            "Before ![d](d.png) after",
            "Before after",
        ),
    ):
        assert quoting.locate(markdown, quote)[1] == words, markdown


def test_a_quote_already_in_the_page_s_words_lands():
    assert quoting.locate(TABLE, "foo bar")[1] == "foo bar"
    wrapped = "# Q\n\nThis is a paragraph\nthat continues here.\n"
    assert quoting.locate(wrapped, "This is a paragraph that continues here.")[1] == (
        "This is a paragraph that continues here."
    )


def test_a_quote_with_a_reference_link_lands_on_its_words():
    # `[zrb][ref]` is only a link because a definition at the end of the document says so;
    # the definition belongs to no block, so it travels with the fragment.
    doc = "# Q\n\nSee [zrb][ref] here.\n\n[ref]: https://x.y\n"
    assert quoting.locate(doc, "See [zrb][ref] here.")[1] == "See zrb here."
    assert quoting.locate(doc, "[zrb][ref]")[1] == "zrb"


def test_a_definition_inside_a_code_fence_is_code_not_a_definition():
    # markdown-it never registers a fenced line as a definition, so the page shows the
    # reference literally -- and the quote, rendered with only the real definitions, says
    # the same thing the page does.
    doc = "# Q\n\nSee [zrb][ref] here.\n\n```\n[ref]: https://x.y\n```\n"
    assert quoting.locate(doc, "See [zrb][ref] here.")[1] == "See [zrb][ref] here."


def test_a_quote_over_two_blocks_is_refused_rather_than_shortened():
    two = "# Q\n\nAlpha paragraph.\n\nBeta paragraph.\n"
    said = quoting.locate(two, "Alpha paragraph.\n\nBeta paragraph.")
    assert isinstance(said, str) and "whole quote" in said


def test_a_quote_no_block_holds_says_what_the_document_holds():
    said = quoting.locate("# Q\n\nAlpha paragraph.\n", "not there at all")
    assert isinstance(said, str) and "No block holds" in said


def test_an_empty_quote_is_refused():
    assert isinstance(quoting.locate("# Q\n\nAlpha.\n", "   "), str)


def test_a_quote_that_shows_no_words_is_refused():
    assert isinstance(quoting.locate("# Q\n\nBefore ![d](d.png) after\n", "![d](d.png)"), str)
