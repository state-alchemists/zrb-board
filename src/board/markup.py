"""A block's words as the page shows them, and how to turn markdown back into them.

Both ends of a note need this. `render.plain_text` gives the visible text a note anchors
against; `digest` places a note beside the source line it came from; `tools` matches a
quote copied out of that read. A quote arrives as words on the page while the source
carries the markup, so the markup has to come off both sides before they can be compared.

An image has no visible words: `plain_text` drops the `<img>` tag and the alt text is not
text on the page, so an image is dropped here too rather than turned into its alt.
"""

import html
import re

_UNWRAP = (
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),  # an image shows nothing
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),  # a link shows its words, not its url
    (re.compile(r"⟦[^⟧]*⟧"), ""),  # a note's own marker, inline in the digest
    (re.compile(r"`+"), ""),
    (re.compile(r"\*\*?([^*]+)\*\*?"), r"\1"),
    (re.compile(r"~~([^~]+)~~"), r"\1"),
    (re.compile(r"(?<!\w)__?([^_]+)__?(?!\w)"), r"\1"),  # `_x_`, not `snake_case`
    (re.compile(r"^\s{0,3}(?:#{1,6}|[-*+]|\d+[.)]|>)\s+", re.M), ""),
)
_SPACE = re.compile(r"\s+")
# A table row, and the rule under the header. `plain_text` renders a row as its cells with
# the tags between them gone, so the source's pipes are not words on the page: a quote
# copied from `| foo | bar |` has to become `foo bar`, and the separator row disappears.
_TABLE_EDGE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\|?(?:\s*:?-{3,}:?\s*\|)+\s*(?::?-{3,}:?)?\|?$")
# A backslash escape: the character after it is shown as itself (`\*` is `*`), so it is
# not markup at all.
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>~|])")
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")


def _table(line: str) -> str:
    """*line* read as a table row: cells separated by spaces, no pipe left to match."""
    if not _TABLE_EDGE.match(line):
        return line
    return "" if _TABLE_SEPARATOR.match(line.strip()) else line.replace("|", " ")


def visible(text: str) -> str:
    """*text* as the page shows it: no markdown, no entities, one space between words."""
    # An escaped character is held aside first, so the unwrapping below cannot take `\*`
    # for emphasis, and put back after it.
    held: list[str] = []

    def hold(match: re.Match) -> str:
        held.append(match.group(1))
        return f"\x00{len(held) - 1}\x00"

    lines = _ESCAPE.sub(hold, text).splitlines()
    stripped = "\n".join(_table(line) for line in lines)
    for pattern, replacement in _UNWRAP:
        stripped = pattern.sub(replacement, stripped)
    restored = _PLACEHOLDER.sub(lambda match: held[int(match.group(1))], stripped)
    # `plain_text` unescapes after it strips tags, so `&amp;` in the source reaches a block
    # as `&`; without this a note quoting "Fish &amp; Chips" matches no block at all.
    return _SPACE.sub(" ", html.unescape(restored)).strip()
