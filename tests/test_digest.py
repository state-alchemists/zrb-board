from board import digest


def _note(quote: str, author: str = "tara", block: str = "b_1") -> dict:
    return {
        "id": "a1",
        "block": block,
        "quote": quote,
        "note": "this is the part I would argue with",
        "author": author,
        "resolved": False,
    }


def test_should_place_the_note_after_the_line_it_quotes(store):
    doc = store.create("0001-question", "# Q\n\nalpha\n\nbeta\n")
    text = digest.build_digest(doc, [_note("beta")])
    lines = text.splitlines()
    quoted = lines.index("beta")
    assert lines[quoted + 1] == ""
    assert "⟦a1 · tara" in lines[quoted + 2]


def test_should_list_an_unplaceable_note_instead_of_guessing(store):
    doc = store.create("0001-question", "# Q\n\nalpha\n")
    text = digest.build_digest(doc, [_note("text that is gone")])
    assert "## unplaced annotations" in text
    assert "text that is gone" not in text.split("## unplaced")[0]


def test_should_mark_a_resolved_note(store):
    doc = store.create("0001-question", "# Q\n\nalpha\n")
    note = {**_note("alpha"), "resolved": True}
    assert "resolved" in digest.build_digest(doc, [note])


def test_should_count_the_annotations_in_the_header(store):
    doc = store.create("0001-question", "# Q\n\nalpha\n")
    text = digest.build_digest(doc, [_note("alpha"), _note("alpha")])
    assert "2 annotation(s)" in text


def test_should_say_when_a_note_carries_a_drawing(store):
    doc = store.create("0001-question", "# Q\n\nalpha\n")
    drawn = {**_note("alpha"), "strokes": [{"points": []}, {"points": []}]}
    assert "✎ 2" in digest.build_digest(doc, [drawn])


def test_should_describe_a_drawing_that_has_no_note(store):
    doc = store.create("0001-question", "# Q\n\nalpha\n")
    drawn = {**_note("alpha"), "note": "", "strokes": [{"points": []}]}
    assert "no note" in digest.build_digest(doc, [drawn])


def test_should_place_a_note_whose_source_line_carries_emphasis(store):
    doc = store.create("0001-question", "# Q\n\n**Go:** when words are not enough\n")
    text = digest.build_digest(doc, [_note("when words are not enough")])
    assert "## unplaced" not in text


def test_should_place_a_note_whose_source_line_carries_a_link(store):
    # A tool sends the block's visible words, which is what the page shows a link as:
    # its text, never its url. The digest has to read the source line the same way.
    doc = store.create("0001-question", "# Q\n\nSee [zrb](https://x.y) for more.\n")
    text = digest.build_digest(doc, [_note("See zrb for more.")])
    assert "## unplaced" not in text


def test_should_place_a_note_on_a_heading_or_a_list_item(store):
    doc = store.create("0001-question", "# Q\n\n## Options\n\n- one\n")
    assert "## unplaced" not in digest.build_digest(
        doc, [_note("Options"), _note("one")]
    )


def test_should_place_a_note_whose_line_carries_an_image(store):
    # An image shows no words at all: the block's text loses the tag, so the quote a
    # tool sends for that line has no image in it either.
    doc = store.create("0001-question", "# Q\n\nBefore ![d](d.png) after\n")
    text = digest.build_digest(doc, [_note("Before after")])
    assert "## unplaced" not in text


def test_should_place_a_note_on_a_quote_that_runs_over_a_hard_wrapped_line(store):
    # The block is one run of words while the source is wrapped, so the search has to see
    # the lines as one: the note belongs beside the line the quote starts on.
    doc = store.create(
        "0001-question", "# Q\n\nThis is a paragraph\nthat continues here.\n"
    )
    text = digest.build_digest(doc, [_note("This is a paragraph that continues here.")])
    lines = text.splitlines()
    quoted = lines.index("This is a paragraph")
    assert "⟦a1 · tara" in lines[quoted + 2]
    assert "## unplaced" not in text


def test_should_place_a_note_on_a_table_row(store):
    # A row is its cells on the page; the source's pipes are not words. The tool sends the
    # words the board renders, and the note lands beside the table's block -- which starts
    # at the header row, because the whole table is one block.
    doc = store.create(
        "0001-question", "# Q\n\n| Name | Value |\n| --- | --- |\n| foo | bar |\n"
    )
    text = digest.build_digest(doc, [_note("foo bar")])
    assert "## unplaced" not in text
    lines = text.splitlines()
    assert lines[lines.index("| Name | Value |") + 2].startswith("⟦a1 · tara")
