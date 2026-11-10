"""A text view of a document with its annotations inline — what the agent reads.

The browser can place a margin note against a paragraph. Text cannot, so the digest puts
each note on the line where the block it belongs to starts, and lists anything it could
not place under its own heading rather than guessing.

Which block a note belongs to is `anchors.resolve`'s answer, the same one the page uses,
so the text and the browser never disagree about where a note sits. The block's source
line comes from the renderer (`render.Block.line`), which is why the document is rendered
here: words the page shows and a line the file holds have to come from one parse.
"""

from __future__ import annotations

from board import anchors, render
from board.store import Doc

RESOLVED_MARK = " · resolved"


def build_digest(doc: Doc, annotations: list[dict]) -> str:
    blocks = render.render(doc.markdown).blocks
    lines = doc.markdown.splitlines()
    placed, unplaced = _place(annotations, blocks)
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
    strokes = len(annotation.get("strokes") or [])
    if strokes and not note:
        note = f"✎ {strokes} stroke(s), no note"
    elif strokes:
        note = f"✎ {strokes} · {note}"
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
    annotations: list[dict], blocks: list[render.Block]
) -> tuple[dict[int, list[str]], list[str]]:
    """Where each note goes: the line its block starts on, or the lost-and-found."""
    line_of = {block.id: block.line for block in blocks}
    placed: dict[int, list[str]] = {}
    unplaced: list[str] = []
    for annotation in annotations:
        match = anchors.resolve(annotation, blocks)
        index = line_of.get(match[0]) if match else None
        if index is None:
            unplaced.append(format_note(annotation))
            continue
        placed.setdefault(index, []).append(format_note(annotation))
    return placed, unplaced
