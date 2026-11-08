"""Resolve an annotation's anchor against the current rendering.

An anchor is a quote, not a coordinate. The block id is the fast path; when
the block was rewritten the id changes, so we fall back to finding the quoted
text elsewhere. When the text is gone too, the annotation is an orphan and
`None` says so — it is never placed somewhere that merely looks close.
"""

from __future__ import annotations

from board.render import Block


def resolve(anchor: dict, blocks: list[Block]) -> tuple[str, int] | None:
    """Return `(block_id, offset)` for the anchor, or `None` if it is an orphan."""
    quote = anchor.get("quote") or ""
    if not quote.strip():
        return None
    preferred = _in_preferred_block(anchor.get("block"), quote, blocks)
    if preferred is not None:
        return preferred
    return _best_quote_match(quote, blocks)


def _in_preferred_block(
    block_id: str | None, quote: str, blocks: list[Block]
) -> tuple[str, int] | None:
    for block in blocks:
        if block.id != block_id:
            continue
        offset = block.text.find(quote)
        return (block.id, offset) if offset >= 0 else None
    return None


def _best_quote_match(quote: str, blocks: list[Block]) -> tuple[str, int] | None:
    """Prefer the shortest block holding the quote — the tightest fit."""
    best: tuple[str, int] | None = None
    best_size = 0
    for block in blocks:
        offset = block.text.find(quote)
        if offset < 0:
            continue
        if best is None or len(block.text) < best_size:
            best, best_size = (block.id, offset), len(block.text)
    return best
