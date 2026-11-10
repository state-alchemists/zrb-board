from board.render import plain_text, render


def test_should_keep_block_ids_stable_across_renders(sample):
    first = [block.id for block in render(sample).blocks]
    second = [block.id for block in render(sample).blocks]
    assert first == second


def test_should_give_identical_blocks_distinct_ids(sample):
    ids = [block.id for block in render(sample).blocks]
    assert len(ids) == len(set(ids))


def test_should_strip_markup_from_block_text():
    assert plain_text("<p>Some <strong>bold</strong> text.</p>") == "Some bold text."


def test_should_wrap_every_block_with_its_id(sample):
    rendered = render(sample)
    for block in rendered.blocks:
        assert f'data-block="{block.id}"' in rendered.html


def test_should_say_where_each_block_starts_in_the_source():
    # The digest puts a note beside the line its block starts on, so the renderer has to
    # say which line that is.
    blocks = render("# Q\n\nalpha\n\nbeta\n").blocks
    assert [block.line for block in blocks] == [0, 2, 4]


def test_should_not_render_raw_html_from_the_markdown():
    rendered = render("<script>alert(1)</script>")
    assert "<script>" not in rendered.html
