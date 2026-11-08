"""A text view of a document with its annotations inline — what the agent reads.

The browser can place a margin note against a paragraph. Text cannot, so the
digest puts each note on the line where its quote ends, and lists anything it
could not place under its own heading rather than guessing.
"""

from __future__ import annotations

import re

from board.store import Doc

_SPACE = re.compile(r"\s+")
QUOTE_PREFIX = 24
RESOLVED_MARK = " · resolved"


def build_digest(doc: Doc, annotations: list[dict]) -> str:
    lines = doc.markdown.splitlines()
    placed, unplaced = _place(annotations, lines)
    header = [
        f"# {doc.title}",
        f"rev {doc.rev} · updated {doc.updated.isoformat(timespec='minutes')}"
        f" · {len(annotations)} annotation(s)",
        f"source: docs/{doc.name}.md",
        "",
    ]
    body = _interleave(lines, placed)
    tail = ["", "## unplaced annotations", *unplaced] if unplaced else []
    return "\n".join([*header, *body, *tail]).rstrip() + "\n"


def format_note(annotation: dict) -> str:
    mark = RESOLVED_MARK if annotation.get("resolved") else ""
    note = " ".join(annotation.get("note", "").split())
    return (
        f"⟦{annotation['id']} · {annotation['author']}{mark}"
        f" · block {annotation['block']} — {note}⟧"
    )


def _interleave(lines: list[str], placed: dict[int, list[str]]) -> list[str]:
    body: list[str] = []
    for index, line in enumerate(lines):
        body.append(line)
        for marker in placed.get(index, []):
            body.extend(["", marker])
    return body


def _place(
    annotations: list[dict], lines: list[str]
) -> tuple[dict[int, list[str]], list[str]]:
    placed: dict[int, list[str]] = {}
    unplaced: list[str] = []
    for annotation in annotations:
        index = _line_index(annotation.get("quote", ""), lines)
        if index is None:
            unplaced.append(format_note(annotation))
            continue
        placed.setdefault(index, []).append(format_note(annotation))
    return placed, unplaced


def _line_index(quote: str, lines: list[str]) -> int | None:
    """The line holding the start of the quote, ignoring markdown syntax."""
    needle = _SPACE.sub(" ", quote).strip()[:QUOTE_PREFIX]
    if not needle:
        return None
    for index, line in enumerate(lines):
        if needle in _SPACE.sub(" ", line):
            return index
    return None
