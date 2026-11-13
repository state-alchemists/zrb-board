"""The board's tools for an agent: what is waiting, what a document says, how to write it back.

These speak the board's own HTTP API instead of importing the server, so a board that is
not running costs a clear error at call time rather than a broken import at startup, and
the tools can live in the same tree as the routes they call -- a route that changes here
changes its caller in the same diff.

Nothing imports this module by itself. `board/zrb_init.py` registers the seven tools with
zrb; the server (`board.app`) runs without zrb ever being installed.

`BOARD_URL` overrides the address (the one `zrb-board` binds by default); `BOARD_AUTHOR`
signs a note, `agent` unless it is set.

Every tool here also takes a `purpose` and ignores it. zrb's own tools ask the model to say
what each call is *for*, so it says it on every call, board tools included; a tool that does
not declare the argument has the call refused as an extra one -- `extra_forbidden`, before
the body runs -- and the board is never reached. Declaring it costs nothing, because no tool
reads it: zrb's narrator takes it from the call's arguments, which is all it was ever for.
The wording lives here rather than in an import of zrb's own `Purpose`, so the server keeps
running with zrb uninstalled.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from board import config


def _default_url() -> str:
    """Where the server listens, read from the settings that bind it.

    `ZRB_BOARD_HOST` and `ZRB_BOARD_PORT` are the server's own, so a board moved to another
    port is still found; `config.url()` is the one place that turns the bind address into
    something connectable, and the command line asks it too.
    """
    return config.url()


BOARD_URL = (os.environ.get("BOARD_URL") or _default_url()).rstrip("/")
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


def _is_conflict(payload: object) -> bool:
    """Whether a 409 body carries the current document, as `store.RevConflict` writes it.

    The keys are what make it a conflict, not the values: a document can be empty, and
    then `markdown` is a perfectly good empty string.
    """
    return isinstance(payload, dict) and "rev" in payload and "markdown" in payload


def _request(method: str, path: str, payload: dict | None = None) -> tuple[str, str]:
    """Call the board, returning `(content type, body)` or raising why it failed."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"{BOARD_URL}{path}", data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as reply:
            return reply.headers.get_content_type(), reply.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as failure:
        # A read timeout is not an HTTPError, and urllib does not always wrap it in a
        # URLError: it reaches here bare. All of them mean the same thing to a caller.
        if isinstance(failure, urllib.error.HTTPError):
            detail = failure.read().decode("utf-8", "replace")
            payload = None
            try:
                payload = json.loads(detail)
            except json.JSONDecodeError:
                pass
            if isinstance(payload, dict) and _is_conflict(payload):
                raise BoardConflict(payload) from None
            # A refusal from the board is written for the caller; its `detail` is the
            # sentence, and the JSON around it is noise.
            if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                detail = payload["detail"]
            raise RuntimeError(
                f"[SYSTEM SUGGESTION] the board answered {method} {path} with HTTP "
                f"{failure.code}: {detail[:2000]}"
            ) from None
        reason = getattr(failure, "reason", failure)
        raise RuntimeError(
            f"[SYSTEM SUGGESTION] the board is not answering at {BOARD_URL} within "
            f"{REQUEST_TIMEOUT}s ({reason}); it is started with `zrb-board`."
        ) from None


def _json(method: str, path: str, payload: dict | None = None) -> Any:
    return json.loads(_request(method, path, payload)[1])


def _document_path(name: str, suffix: str = "") -> str:
    return f"/api/docs/{urllib.parse.quote(name)}{suffix}"


def _is_the_read_view(markdown: str, name: str, rev: str) -> bool:
    """Whether *markdown* is the view BoardRead printed for **this** document.

    The view opens with three lines nothing else does -- a title, a `rev … · updated …`
    line, and the file it came from -- and it names *this* document at *this* revision.
    Checking the revision as well is what keeps a page that merely quotes a digest of its
    own (a sample, a screenshot of one) from being mistaken for the view and refused.
    """
    lines = markdown.splitlines()[:3]
    return (
        len(lines) == 3
        and lines[0].startswith("# ")
        and lines[1].startswith(f"rev {rev} · updated ")
        and lines[2] == f"source: docs/{name}.md"
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


def _is_note(annotation: dict) -> bool:
    """Whether an annotation is a written note rather than ink.

    Ink lives in the same list -- `store.set_ink` writes `kind: "ink"`, no note and
    `resolved: False` -- so counting the list counts a drawing as a question.
    """
    return annotation.get("kind", "note") == "note"


def _mark_document(text: str) -> str:
    """The document one mark names: the first word after `board:`.

    A mark may carry the passage it was made on after an em dash, so the name is the first
    word rather than the whole remainder — otherwise two marks on two paragraphs of the
    same document would count as two documents, and the ×N on a page would stop adding up.
    """
    said = text.removeprefix("board:").strip()
    return said.partition(" — ")[0].strip() or "the board"


def _marks_waiting(inbox: list[str]) -> list[str]:
    """One line per document still marked, the newest mark's time kept.

    The marker is only ever appended to (`store.notify`), and retiring is a separate
    act a caller has to ask for (`store.retire_marks`), so an inbox nobody has retired
    holds every mark ever made -- and then a document's count is marks made, not marks
    waiting. Cutting it to the last few lines loses the one thing the list is for --
    which documents are waiting -- because the newest marks can all be on the same
    page. Folded by document, nothing waiting goes unseen.
    """
    waiting: dict[str, dict] = {}
    for line in inbox:
        stamp, _, text = line.strip().removeprefix("- ").partition(" — ")
        name = _mark_document(text)
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


def BoardState(purpose: str = "") -> str:
    """Look at the board: the marks waiting, then every document on it.

    Read this first whenever the user mentions the board -- the document names it prints
    are what the other board tools take.

    Args:
      purpose: One short phrase saying what this call is for, as zrb asks of every tool.
        Nothing here reads it: it is declared so a call that fills it is not refused.
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


def BoardRead(name: str, source: bool = False, purpose: str = "") -> str:
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
      purpose: One short phrase saying what this call is for, as zrb asks of every tool.
        Nothing here reads it: it is declared so a call that fills it is not refused.
    """
    if not source:
        return _request("GET", _document_path(name, "/state.md"))[1]
    return _json("GET", _document_path(name))["markdown"]


def BoardCreate(name: str, markdown: str, purpose: str = "") -> str:
    """Add a new document to the board, under a name nothing else uses.

    BoardWrite only replaces a document that is already there -- a name the board does
    not know is a 404 -- so this is the one way to give a question a page.

    Args:
      name: The new document's name, e.g. `0013-what-a-release-must-carry`. Letters,
        digits, dots, dashes and underscores, starting with a letter or a digit; a
        name that could leave `docs/` is refused rather than written.
      markdown: The complete text of the new document, starting with a `# title`.
      purpose: One short phrase saying what this call is for, as zrb asks of every tool.
        Nothing here reads it: it is declared so a call that fills it is not refused.
    """
    if not markdown.strip():
        raise RuntimeError(
            "[SYSTEM SUGGESTION] nothing created: a document with no text is a name "
            "with nothing under it. Send the complete text, starting with a `# title`."
        )
    created = _json("POST", "/api/docs", {"name": name, "markdown": markdown})
    return f"Created. {name} is now at rev {created['rev']}, updated {created['updated']}."


def BoardWrite(name: str, markdown: str, rev: str, purpose: str = "") -> str:
    """Replace a document's markdown, stating the revision that was read.

    The whole document is replaced, so send the full text rather than a fragment. If the
    document moved since the read, nothing is written and the current text comes back for
    a merge.

    Args:
      name: A document name, as BoardState prints it.
      markdown: The complete new text of the document.
      rev: The revision BoardRead printed, e.g. `f5d1e63f1a70`.
      purpose: One short phrase saying what this call is for, as zrb asks of every tool.
        Nothing here reads it: it is declared so a call that fills it is not refused.
    """
    if _is_the_read_view(markdown, name, rev):
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


def BoardNote(name: str, quote: str, note: str, purpose: str = "") -> str:
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
      purpose: One short phrase saying what this call is for, as zrb asks of every tool.
        Nothing here reads it: it is declared so a call that fills it is not refused.
    """
    document = _json(
        "POST",
        _document_path(name, "/notes"),
        {"quote": quote, "note": note, "author": NOTE_AUTHOR},
    )
    return f"Noted on block {document['block']}: {note}"


def BoardResolveNote(name: str, note_id: str, purpose: str = "") -> str:
    """Close a note that has been dealt with, by the id BoardRead shows.

    Args:
      name: A document name, as BoardState prints it.
      note_id: The note's id, e.g. `a1`.
      purpose: One short phrase saying what this call is for, as zrb asks of every tool.
        Nothing here reads it: it is declared so a call that fills it is not refused.
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


def BoardRetireMarks(upto: str = "", id: str = "", purpose: str = "") -> str:
    """Retire inbox marks that have been dealt with, so "waiting" means waiting.

    A mark is only ever appended to, so BoardState counts every mark ever made: "×8"
    on a document is eight marks, not eight still waiting. Retire them once the work
    they asked for is done. It is a separate call on purpose -- reading the board is
    not answering it, and a mark cleared too early is a reminder thrown away.

    Args:
      upto: The newest mark to retire, by the timestamp BoardState prints beside it;
        every mark at or before that stamp goes with it. Empty retires every mark, but
        only when no `id` is given.
      id: The one mark to retire, by the id the nudge puts in the message it hands you.
        Use this when answering a single mark: a stamp is only good to the second, so
        two marks made together share one, and `upto` would take marks nobody has read.
      purpose: One short phrase saying what this call is for, as zrb asks of every tool.
        Nothing here reads it: it is declared so a call that fills it is not refused.
    """
    retired = _json("POST", "/api/inbox/retire", {"upto": upto, "id": id})["retired"]
    if not retired:
        if id:
            return "Nothing retired: no mark in the inbox has that id any more."
        return "Nothing retired: no mark in the inbox is at or before that time."
    return f"Retired {retired} mark{'' if retired == 1 else 's'}. BoardState lists only what is left."
