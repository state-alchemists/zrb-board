"""The board's tools for an agent: what is waiting, what a document says, how to write it back.

These speak the board's own HTTP API instead of importing the server, so a board that is
not running costs a clear error at call time rather than a broken import at startup, and
the tools can live in the same tree as the routes they call -- a route that changes here
changes its caller in the same diff.

Nothing imports this module by itself. `board/zrb_init.py` registers the five tools with
zrb; the server (`board.app`) runs without zrb ever being installed.

`BOARD_URL` overrides the address (the one `zrb-board` binds by default); `BOARD_AUTHOR`
signs a note, `agent` unless it is set.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from board.markup import visible as _visible

BOARD_URL = os.environ.get("BOARD_URL", "http://127.0.0.1:8765").rstrip("/")
REQUEST_TIMEOUT = 15
NOTE_AUTHOR = os.environ.get("BOARD_AUTHOR", "agent")
# A note anchors against a block's *visible* text, while `BoardRead` hands back the
# markdown source (`render.plain_text`, `digest.build_digest`). `board.markup.visible` is
# the one definition of that text, so a quote copied out of the read -- bold, a link's
# url, a heading's hashes, a line break in the middle -- turns back into words the block
# holds before it is matched.


class BoardConflict(RuntimeError):
    """The document moved between the read and the write."""

    def __init__(self, payload: dict) -> None:
        super().__init__("the document changed since it was read")
        self.rev = payload.get("rev", "")
        self.markdown = payload.get("markdown", "")


def _request(method: str, path: str, payload: dict | None = None) -> tuple[str, str]:
    """Call the board, returning `(content type, body)` or raising why it failed."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"{BOARD_URL}{path}", data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as reply:
            return reply.headers.get_content_type(), reply.read().decode("utf-8")
    except urllib.error.HTTPError as failure:
        detail = failure.read().decode("utf-8", "replace")
        if failure.code == 409:
            try:
                raise BoardConflict(json.loads(detail)) from None
            except json.JSONDecodeError:
                pass
        raise RuntimeError(
            f"[SYSTEM SUGGESTION] the board answered {method} {path} with HTTP "
            f"{failure.code}: {detail[:2000]}"
        ) from None
    except urllib.error.URLError as failure:
        raise RuntimeError(
            f"[SYSTEM SUGGESTION] the board is not answering at {BOARD_URL} "
            f"({failure.reason}); it is started with `zrb-board`."
        ) from None


def _json(method: str, path: str, payload: dict | None = None) -> Any:
    return json.loads(_request(method, path, payload)[1])


def _document_path(name: str, suffix: str = "") -> str:
    return f"/api/docs/{urllib.parse.quote(name)}{suffix}"


# The view `digest.build_digest` writes opens with three lines nothing else does: a title,
# a revision line, and the file it came from. Only the start is read -- a document may
# quote a digest of its own further down, and that is the document's business.
_DIGEST_REV = re.compile(r"^rev \w+ · updated \S+ · \d+ annotation\(s\)$")
_DIGEST_SOURCE = re.compile(r"^source: docs/\S+\.md$")


def _is_the_read_view(markdown: str) -> bool:
    """Whether *markdown* is what BoardRead printed, rather than a document.

    Writing the view back would make its title, revision line, source line and note
    markers part of the document for good. BoardRead(source=True) is the text BoardWrite
    takes; this only catches a caller who pasted the default read by mistake.
    """
    lines = markdown.splitlines()[:3]
    return (
        len(lines) == 3
        and lines[0].startswith("# ")
        and bool(_DIGEST_REV.match(lines[1]))
        and bool(_DIGEST_SOURCE.match(lines[2]))
    )


def _held(block_text: str, needle: str) -> str | None:
    """The longest run of *needle*'s leading words that *block_text* holds verbatim.

    What comes back is cut from *block_text*, never from *needle*: the server anchors a
    note with `block.text.find(quote)` (`anchors.resolve`), so the quote has to be the
    block's own words or the note lands nowhere. A quote that spans two paragraphs, or
    whose last sentence was edited away, then anchors on as much of itself as is really
    there instead of failing whole.
    """
    words = needle.split(" ")
    for size in range(len(words), 0, -1):
        candidate = " ".join(words[:size])
        if candidate in block_text:
            return candidate
    return None


def _block_holding(blocks: list[dict], quote: str) -> tuple[dict, str]:
    """The tightest block that holds the whole quote, and the quote as that block holds it.

    The whole of it, not a prefix: a quote taken from a read the document has since moved
    past would otherwise be pinned to whichever paragraph opens with the same words, and
    the note would read as if it were about a sentence nobody wrote. When no block holds
    it, the error names the block holding the most of it, so the caller can quote that
    passage exactly and say it again.
    """
    needle = _visible(quote)
    if not needle:
        raise RuntimeError(
            "[SYSTEM SUGGESTION] a note needs a quote: the words it is about, copied "
            "from BoardRead."
        )
    holding = [
        (block, held)
        for block in blocks
        if (held := _held(block["text"], needle)) is not None
    ]
    if not holding:
        excerpts = "; ".join(block["text"][:60] for block in blocks[:6])
        raise RuntimeError(
            f"[SYSTEM SUGGESTION] no block holds {needle!r}. Quote words that are "
            f"on the page; the document begins: {excerpts}"
        )
    whole = [(block, held) for block, held in holding if held == needle]
    if not whole:
        # The block holding most of the quote, and the tightest of those on a tie: it
        # holds the passage the caller meant, so it is the one to quote back.
        block, partial = max(
            holding, key=lambda pair: (len(pair[1]), -len(pair[0]["text"]))
        )
        raise RuntimeError(
            f"[SYSTEM SUGGESTION] no block holds the whole quote, so nothing is noted. "
            f"Block {block['id']} holds {partial!r}: quote exactly that if it is the "
            "passage you mean. A quote that runs over two paragraphs has to be cut at "
            "the boundary -- the board anchors inside one block."
        )
    return min(whole, key=lambda pair: len(pair[0]["text"]))


def _is_note(annotation: dict) -> bool:
    """Whether an annotation is a written note rather than ink.

    Ink lives in the same list -- `store.set_ink` writes `kind: "ink"`, no note and
    `resolved: False` -- so counting the list counts a drawing as a question.
    """
    return annotation.get("kind", "note") == "note"


def _marks_waiting(inbox: list[str]) -> list[str]:
    """One line per document still marked, the newest mark's time kept.

    The marker is only ever appended to (`store.notify`) and nothing clears it, so the
    folder holds every mark ever made. Cutting it to the last few lines loses the one
    thing the list is for -- which documents are waiting -- because the newest marks can
    all be on the same page. Folded by document, nothing waiting goes unseen.
    """
    waiting: dict[str, dict] = {}
    for line in inbox:
        stamp, _, text = line.strip().removeprefix("- ").partition(" — ")
        name = text.removeprefix("board:").strip() or "the board"
        entry = waiting.setdefault(name, {"count": 0, "newest": stamp})
        entry["count"] += 1
        entry["newest"] = stamp  # the folder is oldest first, so the last read is newest
    lines = []
    for name, entry in sorted(
        waiting.items(), key=lambda item: item[1]["newest"], reverse=True
    ):
        times = (
            f"- {name} — {entry['newest']}"
            if entry["count"] == 1
            else f"- {name} ×{entry['count']} — newest {entry['newest']}"
        )
        lines.append(times)
    return lines or ["(no marks waiting)"]


def BoardState() -> str:
    """Look at the board: the marks waiting, then every document on it.

    Read this first whenever the user mentions the board -- the document names it prints
    are what the other board tools take.
    """
    inbox = [
        line for line in _request("GET", "/api/inbox")[1].splitlines() if line.strip()
    ]
    documents = _json("GET", "/api/docs")["docs"]
    lines = ["## marks waiting", "", *_marks_waiting(inbox), "", "## documents", ""]
    if not documents:
        lines.append("(the board holds no documents yet)")
    for document in documents:
        open_notes = [
            note
            for note in document["annotations"]
            if _is_note(note) and not note.get("resolved")
        ]
        ink = sum(1 for item in document["annotations"] if not _is_note(item))
        drawn = f", ink on {ink} block(s)" if ink else ""
        lines.append(
            f"- {document['name']} — {document['title']} "
            f"(rev {document['rev']}, updated {document['updated']}, "
            f"{len(open_notes)} open note(s){drawn})"
        )
    return "\n".join(lines)


def BoardRead(name: str, source: bool = False) -> str:
    """Read one document: the readable view, or the document's own markdown.

    By default it is the view: the title, the revision, and each note in the margin
    beside the passage it is about, as `⟦id · author · block …⟧`. Read and quote from it.
    It is *not* what BoardWrite takes -- its header and markers are the view's, not the
    document's words.

    Args:
      name: A document name, as BoardState prints it.
      source: True gives the document's own markdown, byte for byte, with nothing added
        around it: that is the text BoardWrite takes back. Its revision is the one
        BoardState prints for the document, or the first line of the default view. Use
        the source to write, and the view to think.
    """
    if not source:
        return _request("GET", _document_path(name, "/state.md"))[1]
    return _json("GET", _document_path(name))["markdown"]


def BoardWrite(name: str, markdown: str, rev: str) -> str:
    """Replace a document's markdown, stating the revision that was read.

    The whole document is replaced, so send the full text rather than a fragment. If the
    document moved since the read, nothing is written and the current text comes back for
    a merge.

    Args:
      name: A document name, as BoardState prints it.
      markdown: The complete new text of the document.
      rev: The revision BoardRead printed, e.g. `f5d1e63f1a70`.
    """
    if _is_the_read_view(markdown):
        raise RuntimeError(
            "[SYSTEM SUGGESTION] nothing written: that is BoardRead's view of the "
            f"document, not the document -- its title line, its rev line, its source "
            f"line and its note markers would become words on the page. Take the text "
            f"from BoardRead({name!r}, source=True) and edit that."
        )
    if not markdown.strip():
        raise RuntimeError(
            "[SYSTEM SUGGESTION] nothing written: an empty document would replace all "
            f"of {name}. Send the complete text, including the parts that stay."
        )
    path = _document_path(name)
    try:
        written = _json("PUT", path, {"markdown": markdown, "rev": rev})
    except BoardConflict as conflict:
        return (
            f"Not written: the document moved since rev {rev} and is now at rev "
            f"{conflict.rev}. Merge into the current text below, then write again "
            f"with that revision.\n\n{conflict.markdown}"
        )
    return f"Written. {name} is now at rev {written['rev']}, updated {written['updated']}."


def BoardNote(name: str, quote: str, note: str) -> str:
    """Leave a note on a quoted passage, in the margin beside it.

    Use it for a remark about one paragraph -- a question, a correction, an objection --
    rather than rewriting the document under the user's words.

    Args:
      name: A document name, as BoardState prints it.
      quote: The words the note is about, copied from BoardRead's text -- markdown and
        all: it is matched against the block's visible words, so bold, a link, a heading's
        hashes or a line break in the middle do not stop it. It has to be words one block
        holds whole; a quote that runs over two paragraphs is refused, not shortened.
      note: What to say about them.
    """
    document = _json("GET", _document_path(name))
    block, held = _block_holding(document["blocks"], quote)
    _json(
        "POST",
        _document_path(name, "/annotations"),
        {"block": block["id"], "quote": held, "note": note, "author": NOTE_AUTHOR},
    )
    return f"Noted on block {block['id']}: {note}"


def BoardResolveNote(name: str, note_id: str) -> str:
    """Close a note that has been dealt with, by the id BoardRead shows.

    Args:
      name: A document name, as BoardState prints it.
      note_id: The note's id, e.g. `a1`.
    """
    document = _json("GET", _document_path(name))
    found = next(
        (item for item in document["annotations"] if item.get("id") == note_id), None
    )
    if found is None:
        raise RuntimeError(
            f"[SYSTEM SUGGESTION] {name} has no annotation {note_id!r}. BoardState counts "
            "them and BoardRead lists them as ⟦id · author · block …⟧."
        )
    if not _is_note(found):
        # Ink is in the same list; closing it would mark the drawing dealt with and
        # leave it drawn, which is a worse lie than refusing.
        raise RuntimeError(
            f"[SYSTEM SUGGESTION] {note_id} on {name} is ink ({found.get('author')} drew "
            f"{len(found.get('strokes') or [])} stroke(s)), not a note: there is nothing "
            "to close. Erase it on the page if it should go."
        )
    _json(
        "POST",
        _document_path(name, f"/annotations/{urllib.parse.quote(note_id)}/resolve"),
    )
    return f"Closed note {note_id} on {name}."
