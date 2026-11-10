"""A block's words as the page shows them, and how to turn markdown back into them.

Both ends of a note need this. `render.plain_text` gives the visible text a note anchors
against; `digest` places a note beside the source line it came from; `tools` matches a
quote copied out of that read. A quote arrives as words on the page while the source
carries the markup, so the markup has to come off both sides before they can be compared.

An image has no visible words: `plain_text` drops the `<img>` tag and the alt text is not
text on the page, so an image is dropped here too rather than turned into its alt.
"""

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


def visible(text: str) -> str:
    """*text* as the page shows it: no markdown, one space between the words."""
    stripped = text
    for pattern, replacement in _UNWRAP:
        stripped = pattern.sub(replacement, stripped)
    return _SPACE.sub(" ", stripped).strip()
