"""Where a quote copied out of the source lands on the page.

The file and the page read markdown differently. `| foo | bar |` is a table row only under
its header; `\\*` is a star; `[zrb][ref]` is a word that depends on a definition further
down; `![d](d.png)` is no words at all. So a quote is rendered *where it stands* before it
is matched -- under an empty header when it is a row -- and the words that come back are
the words the block holds.

A quote already in the words the page shows -- a selection, or one the agent copied after
a wrap -- is matched as it is, against the block text the page renders.
"""

from __future__ import annotations

from board import render


def locate(markdown: str, quote: str) -> tuple[str, str] | str:
    """`(block id, the words it holds)`, or a sentence saying why no block does."""
    blocks = render.render(markdown).blocks
    if not quote.strip():
        return "A note needs a quote: the words it is about, copied from BoardRead."
    at = markdown.find(quote)
    if at < 0:
        # Already in the page's words: a selection, or a quote copied after a wrap.
        return match(blocks, " ".join(quote.split()))
    return match(blocks, _where_it_stands(markdown, at, quote, blocks))


def _in_code(markdown: str, at: int, blocks: list[render.Block]) -> bool:
    """Whether a quote starting at *at* begins inside a code block."""
    line = markdown.count("\n", 0, at)
    holder = None
    for block in blocks:
        if block.line > line:
            break
        holder = block
    return bool(holder and holder.code)


def _definitions(markdown: str) -> str:
    """The document's link reference definitions, in source form, or "".

    A definition belongs to no block and sits at the end of the document, so a quote
    holding `[zrb][ref]` needs them to render as the page renders it. They are taken from
    the parser, not from the text: a line that looks like one inside a fence is code.
    """
    lines = []
    for label, found in render.references(markdown).items():
        if not isinstance(found, dict):
            continue
        href = found.get("href")
        if not href:
            continue
        title = found.get("title")
        suffix = f' "{title}"' if title else ""
        lines.append(f"[{label}]: {href}{suffix}")
    return "\n".join(lines)


def _where_it_stands(
    markdown: str, at: int, quote: str, blocks: list[render.Block]
) -> str:
    """The quote's words, rendered where it stands rather than on its own.

    Three constructs need the document around them. A table row means nothing alone --
    markdown reads `| foo | bar |` by itself as a paragraph -- so a row is rendered under
    an empty header, which is the one thing its own table gives it. A reference link is
    resolved by a definition that lives at the end of the document and belongs to no
    block, so the definitions travel with the fragment. And a quote taken from a code
    block is code: `**foo**` in a fence is those seven characters on the page, so it keeps
    them, where rendering it as markdown would turn it into a bold `foo` and match
    nothing.
    """
    if _in_code(markdown, at, blocks):
        return " ".join(quote.split())
    definitions = _definitions(markdown)
    context = f"{quote}\n\n{definitions}" if definitions else quote
    line = markdown[:at].rsplit("\n", 1)[-1] + quote
    if line.lstrip().startswith("|"):
        rendered = render.render(f"|  |  |\n| --- | --- |\n{context}").blocks
    else:
        rendered = render.render(context).blocks
    return " ".join(block.text for block in rendered if block.text)


def match(blocks: list[render.Block], needle: str) -> tuple[str, str] | str:
    """The tightest block that holds *needle* whole, or a sentence saying why none does."""
    if not needle:
        return "A note needs a quote: the words it is about, copied from BoardRead."
    holding = [
        (block, held)
        for block in blocks
        if (held := _held(block.text, needle)) is not None
    ]
    if not holding:
        excerpts = "; ".join(block.text[:60] for block in blocks[:6])
        return (
            f"No block holds {needle!r}. Quote words that are on the page; the document "
            f"begins: {excerpts}"
        )
    whole = [(block, held) for block, held in holding if held == needle]
    if not whole:
        block, partial = max(
            holding, key=lambda pair: (len(pair[1]), -len(pair[0].text))
        )
        return (
            f"No block holds the whole quote. Block {block.id} holds {partial!r}: quote "
            "exactly that if it is the passage you mean."
        )
    block, held = min(whole, key=lambda pair: len(pair[0].text))
    return block.id, held


def _held(block_text: str, needle: str) -> str | None:
    """The longest run of *needle*'s leading words that *block_text* holds verbatim."""
    words = needle.split(" ")
    for size in range(len(words), 0, -1):
        candidate = " ".join(words[:size])
        if candidate in block_text:
            return candidate
    return None
