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


def test_a_quote_from_a_code_block_keeps_its_own_characters():
    # A fence shows its lines as they are, so `**foo**` there is seven characters and not
    # a bold word; the quote keeps them. The same words in prose are rendered as markdown.
    fenced = "# Q\n\n```\n**foo**\n```\n"
    assert quoting.locate(fenced, "**foo**")[1] == "**foo**"
    assert quoting.locate(fenced, "**foo**")[0].startswith("b_")
    assert quoting.locate("# Q\n\n**foo**\n", "**foo**")[1] == "foo"


def test_a_quote_from_an_indented_code_block_is_code_too():
    doc = "# Q\n\n    **foo**\n"
    assert quoting.locate(doc, "**foo**")[1] == "**foo**"


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


TWICE = "# Q\n\n```\n**foo**\n```\n\n**foo** in prose\n"


def test_a_quote_that_stands_twice_takes_the_first_occurrence():
    # The document from the issue: `**foo**` in a fence is those characters on the page,
    # the same source in a paragraph is a bold word, so the two are different blocks and
    # the quote fits both. Without a hint the first is taken, and `also_at` says so.
    placed = quoting.placing(TWICE, "**foo**")
    fence = next(block.id for block in render.render(TWICE).blocks if block.code)
    assert (placed.block, placed.words) == (fence, "**foo**")
    assert placed.also_at == (7,)
    assert placed.hint == ""
    # `locate` is the same answer in the shape callers had before hints existed.
    assert quoting.locate(TWICE, "**foo**") == (fence, "**foo**")


def test_a_hint_sends_the_quote_to_the_second_occurrence():
    # The hint is the only thing that can decide it: both occurrences anchor, so trying
    # each until one fits would stop at the fence every time.
    paragraph = next(b for b in render.render(TWICE).blocks if "prose" in b.text)
    placed = quoting.placing(TWICE, "**foo**", near=paragraph.id)
    assert (placed.block, placed.words) == (paragraph.id, "foo")
    assert placed.also_at == (4,)
    assert placed.hint == "matched"


def test_a_line_hint_picks_the_occurrence_that_starts_on_it():
    # Counted from 1, which is how the answer names the other places -- and how an editor
    # counts the source, so `near=7` and `near="7"` mean the same line.
    paragraph = next(b for b in render.render(TWICE).blocks if "prose" in b.text)
    for line in (7, "7"):
        placed = quoting.placing(TWICE, "**foo**", near=line)
        assert placed.block == paragraph.id and placed.hint == "matched"
    assert quoting.placing(TWICE, "**foo**", near=4).words == "**foo**"


def test_a_hint_that_names_nothing_takes_the_first_and_says_it_missed():
    # A note still has to land somewhere, and the first occurrence is where it always
    # landed; what changes is that the answer stops pretending the hint was heard.
    fence = next(block.id for block in render.render(TWICE).blocks if block.code)
    for nothing in ("b_0000000000", 99, "the paragraph about foo"):
        placed = quoting.placing(TWICE, "**foo**", near=nothing)
        assert (placed.block, placed.hint) == (fence, "missed"), nothing


def test_a_hint_cannot_move_a_quote_onto_a_passage_that_does_not_hold_it():
    # It chooses between places the quote already stands; it is not a way to write a note
    # about one paragraph in another paragraph's words.
    doc = "# Q\n\nalpha\n\nbeta\n"
    alpha, beta = (block.id for block in render.render(doc).blocks[1:])
    placed = quoting.placing(doc, "alpha", near=beta)
    assert (placed.block, placed.hint) == (alpha, "missed")


def test_a_quote_that_stands_once_has_nothing_to_choose_between():
    doc = "# Q\n\nOnly once here.\n"
    placed = quoting.placing(doc, "once")
    only = next(b.id for b in render.render(doc).blocks if "once" in b.text)
    assert (placed.block, placed.words) == (only, "once")
    assert (placed.also_at, placed.hint) == ((), "")
