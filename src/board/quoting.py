"""Where a quote copied out of the source lands on the page.

The file and the page read markdown differently. `| foo | bar |` is a table row only under
its header; `\\*` is a star; `[zrb][ref]` is a word that depends on a definition further
down; `![d](d.png)` is no words at all. So a quote is rendered *where it stands* before it
is matched -- under an empty header when it is a row -- and the words that come back are
the words the block holds.

A quote already in the words the page shows -- a selection, or one the agent copied after
a wrap -- is matched as it is, against the block text the page renders.

Which occurrence of a quote is meant is not in the quote. `**foo**` in a fence and `**foo**`
in a paragraph are two places in the source and two different blocks on the page, so the
first `find` is as likely to be the wrong one as the right one -- and both anchor, so
trying each until one fits cannot tell them apart either. `near` is how a caller says where
it read the quote: the block it saw, or the line it starts on. With no hint the first
occurrence is taken, which is the honest default, and `Placement` says how many other
places the same words stand so the answer can own the choice.
"""

from __future__ import annotations

from dataclasses import dataclass

from board import render


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a quote landed, and what else the same words could have meant.

    `also_at` is the source line, counted from one, of every occurrence the note did *not*
    take: empty for a quote that stands once, which is the only case that needs no saying.
    `hint` is `""` when no hint was given, and otherwise whether the hint is what chose this
    occurrence -- `"matched"` -- or named nothing here, so the first was taken instead.
    """

    block: str
    words: str
    also_at: tuple[int, ...] = ()
    hint: str = ""


def locate(
    markdown: str, quote: str, near: str | int = ""
) -> tuple[str, str] | str:
    """`(block id, the words it holds)`, or a sentence saying why no block does.

    The two things `placing` says beyond that -- which other occurrences the words stand
    at, and whether `near` decided between them -- are dropped here for callers that only
    need the block.
    """
    placed = placing(markdown, quote, near)
    return placed if isinstance(placed, str) else (placed.block, placed.words)


def placing(markdown: str, quote: str, near: str | int = "") -> Placement | str:
    """Where the quote lands, or a sentence saying why no block does.

    *near* is a block id, or the line the quote starts on counted from one. It only ever
    chooses between places the quote already stands: the word `foo` cannot be hinted onto a
    paragraph that never held it.
    """
    if not quote.strip():
        return "A note needs a quote: the words it is about, copied from BoardRead."
    blocks = render.render(markdown).blocks
    places = _occurrences(markdown, quote)
    if not places:
        # Already in the page's words: a selection, or a quote copied after a wrap.
        return _placed(match(blocks, " ".join(quote.split())))
    chosen, hint = _chosen(markdown, places, blocks, near)
    words = _where_it_stands(markdown, chosen, quote, blocks)
    found = _landed(_line(markdown, chosen), words, blocks, hint)
    if isinstance(found, str):
        return found
    block, held = found
    return Placement(block, held, _other_lines(markdown, places, chosen), hint)


def _landed(
    line: int, words: str, blocks: list[render.Block], hint: str
) -> tuple[str, str] | str:
    """The block the note goes on: the hinted one when a hint is what chose the occurrence.

    *line* is the occurrence's own source line. `match` takes the tightest block holding the
    words, which is right when nothing else is known -- but a hinted `foo` inside `foo in
    prose` loses to a fence that holds the characters `**foo**`, and the hint would have
    selected the occurrence only for the match to undo it. The block that owns the
    occurrence is the answer the caller gave.
    """
    holder = _block_at(blocks, line)
    if hint == "matched" and holder is not None and words in holder.text:
        return holder.id, words
    return match(blocks, words)


def _in_code(markdown: str, at: int, blocks: list[render.Block]) -> bool:
    """Whether a quote starting at *at* begins inside a code block."""
    holder = _block_at(blocks, _line(markdown, at))
    return bool(holder and holder.code)


def _placed(found: tuple[str, str] | str) -> Placement | str:
    """`match`'s two answers in the shapes `placing` returns."""
    return found if isinstance(found, str) else Placement(*found)


def _occurrences(markdown: str, quote: str) -> list[int]:
    """Every offset where *quote* begins, in document order.

    Overlaps are kept: `aa` in `aaa` is two occurrences, and a caller choosing the second
    means the one that starts one character later.
    """
    found: list[int] = []
    at = markdown.find(quote)
    while at >= 0:
        found.append(at)
        at = markdown.find(quote, at + 1)
    return found


def _chosen(
    markdown: str, places: list[int], blocks: list[render.Block], near: str | int
) -> tuple[int, str]:
    """The occurrence the note takes, and what the hint had to do with it.

    No hint is the first occurrence, which is what every caller got before hints existed.
    A hint that names one of the places takes that one. A hint that names nothing here is
    the first occurrence again -- the note still has to land somewhere -- and the answer
    says the hint missed rather than pretending it was heard.
    """
    hint = str(near).strip()
    if not hint:
        return places[0], ""
    pointed = _pointed_at(markdown, places, blocks, hint)
    return (places[0], "missed") if pointed is None else (pointed, "matched")


def _pointed_at(
    markdown: str, places: list[int], blocks: list[render.Block], hint: str
) -> int | None:
    """The occurrence *hint* names, or `None` when it names none of them.

    A block id is matched against the block that owns the occurrence's line, so a quote
    inside a fence belongs to the fence; digits are a line, counted from one, and only the
    occurrence that starts on it counts. Anything else is a hint this cannot read.
    """
    if hint.isdigit():
        line = int(hint)
        return next((at for at in places if _line(markdown, at) + 1 == line), None)
    for at in places:
        holder = _block_at(blocks, _line(markdown, at))
        if holder is not None and holder.id == hint:
            return at
    return None


def _other_lines(markdown: str, places: list[int], chosen: int) -> tuple[int, ...]:
    """The lines, counted from one, where the same words stand and the note does not."""
    return tuple(_line(markdown, at) + 1 for at in places if at != chosen)


def _block_at(blocks: list[render.Block], line: int) -> render.Block | None:
    """The block that owns source *line*: the last one starting at or before it.

    A block's span is everything up to the next block's first line, which is what makes a
    quote inside a fence the fence's and not the paragraph above it.
    """
    holder = None
    for block in blocks:
        if block.line > line:
            break
        holder = block
    return holder


def _line(markdown: str, at: int) -> int:
    """The 0-based source line of offset *at*, counted as `render.Block.line` counts."""
    return markdown.count("\n", 0, at)


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
