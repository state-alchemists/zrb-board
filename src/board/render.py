"""Render markdown to HTML with a stable id on every top-level block.

The block id is what an annotation is anchored to: a hash of the block's
visible text, so the same paragraph keeps the same id across renders, and a
different paragraph never borrows it.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.token import Token

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")

ID_LENGTH = 10


@dataclass(frozen=True, slots=True)
class Block:
    id: str
    text: str
    html: str


@dataclass(frozen=True, slots=True)
class Rendered:
    html: str
    blocks: list[Block]


def plain_text(fragment: str) -> str:
    """The visible text of an HTML fragment, whitespace collapsed."""
    return _SPACE.sub(" ", html_lib.unescape(_TAG.sub(" ", fragment))).strip()


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": True})
    return md.enable("table").enable("strikethrough")


def _group_blocks(tokens: list[Token]) -> list[list[Token]]:
    """Split the token stream into one group per top-level block."""
    groups: list[list[Token]] = []
    current: list[Token] = []
    for token in tokens:
        current.append(token)
        closes_block = token.level == 0 and token.nesting == -1
        stands_alone = token.level == 0 and token.nesting == 0
        if closes_block or stands_alone:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _block_id(text: str, seen: dict[str, int]) -> str:
    """Hash the text, then number the repeats so identical blocks stay distinct."""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:ID_LENGTH]
    seen[digest] = seen.get(digest, 0) + 1
    suffix = "" if seen[digest] == 1 else f"-{seen[digest]}"
    return f"b_{digest}{suffix}"


def render(markdown: str) -> Rendered:
    md = _markdown()
    seen: dict[str, int] = {}
    blocks: list[Block] = []
    for group in _group_blocks(md.parse(markdown)):
        fragment = md.renderer.render(group, md.options, {})
        text = plain_text(fragment)
        blocks.append(Block(_block_id(text, seen), text, fragment))
    body = "\n".join(
        f'<section class="block" data-block="{block.id}">{block.html}</section>'
        for block in blocks
    )
    return Rendered(body, blocks)
