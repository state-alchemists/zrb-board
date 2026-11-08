from board import anchors
from board.render import render


def _block_text(block_id, blocks):
    return next(block.text for block in blocks if block.id == block_id)


def test_should_resolve_inside_the_named_block(sample):
    blocks = render(sample).blocks
    anchor = {"block": blocks[1].id, "quote": "First paragraph about anchors."}
    assert anchors.resolve(anchor, blocks) == (blocks[1].id, 0)


def test_should_fall_back_to_the_quote_when_the_block_id_is_gone(sample):
    blocks = render(sample).blocks
    anchor = {"block": "b_rewritten", "quote": "Second paragraph, same text."}
    block_id, offset = anchors.resolve(anchor, blocks)
    assert _block_text(block_id, blocks) == "Second paragraph, same text."
    assert offset == 0


def test_should_orphan_an_anchor_whose_text_was_deleted():
    blocks = render("# Only a heading").blocks
    assert anchors.resolve({"block": "b_x", "quote": "never written"}, blocks) is None


def test_should_orphan_an_anchor_without_a_quote(sample):
    blocks = render(sample).blocks
    assert anchors.resolve({"block": blocks[1].id, "quote": "  "}, blocks) is None
