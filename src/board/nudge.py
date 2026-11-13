"""A mark becomes a turn, and the answer lands in the margin beside what was marked.

`board/zrb_init.py` registers `board_nudge` on zrb's chat task, so a passage Go marks in
the browser is answered without anyone typing first. A trigger is a callable returning an
async iterable, and every item it yields is a user turn (`zrb/llm/ui/trigger.py:1-11`).

Three rules this file exists to keep:

- **Off unless switched on.** A mark may start a turn only while `ZRB_BOARD_NUDGE` is set.
  The switch is read at every look, but the trigger is registered once, when the chat
  starts, so switching it on is a new chat and switching it off is the next look.
- **One mark, one turn.** A ledger of consumed stamps sits beside the inbox, so a restart
  cannot answer the same mark twice. Retiring the mark is the turn's own job; the ledger is
  what holds if the turn forgets, or dies halfway.
- **Nothing here imports zrb.** Only `zrb_init.py` needs zrb, so the server, the tests and
  a one-off `python -c` can all read this file.

The inbox is read as a file, not over HTTP: the trigger runs on the machine that holds the
board, and a poll that needed the server answering would fall silent exactly when the
board is down — which is when a mark most deserves to survive.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from board import config

INBOX_NAME = "inbox.md"
LEDGER_NAME = "nudge.json"


@dataclass(frozen=True)
class Mark:
    """One inbox line read: when it was made, which document, and what passage."""

    stamp: str
    name: str
    quote: str = ""

    def prompt(self) -> str:
        """The turn this mark is worth, in words an agent can act on.

        The passage is what makes the answer a note rather than a sentence: `BoardNote`
        anchors by quotation, so a mark that carries none is answered in the chat and says
        so, instead of the turn inventing a quote to hang an answer on.
        """
        retire = f'BoardRetireMarks(upto="{self.stamp}")'
        if not self.quote:
            return (
                f"board: {self.name} is marked, with no passage chosen. "
                f'Read it with BoardRead("{self.name}"), answer here in the chat — there '
                f"is no quotation to anchor a note to — and then retire this mark: {retire}."
            )
        return (
            f"board: {self.name} is marked, on this passage:\n\n"
            f"> {self.quote}\n\n"
            f"Read the document, answer that passage in the margin with "
            f'BoardNote("{self.name}", "{self.quote}", your answer) — the quotation is what '
            f"anchors it — and then retire this mark: {retire}. If that passage can no "
            f"longer be anchored, because the words were edited or moved, answer in the "
            f"chat and say so rather than guessing at a quotation."
        )


def read_inbox_marks(meta: Path) -> list[Mark]:
    """Every mark in the inbox, oldest first, read the way the inbox writes them."""
    path = meta / INBOX_NAME
    if not path.is_file():
        return []
    marks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        mark = _read_mark_line(line)
        if mark is not None:
            marks.append(mark)
    return marks


def _read_mark_line(line: str) -> Mark | None:
    """`- <stamp> — board: <name> — <passage>`, or `None` for a line that is not a mark.

    A mark from before the passage was carried reads `board: <name>` and nothing else, so
    the em dash and the quotation are both optional here.
    """
    stamp, _, text = line.strip().removeprefix("- ").partition(" — ")
    if not stamp or not text.startswith("board:"):
        return None
    said = text.removeprefix("board:").strip()
    name, _, quote = said.partition(" — ")
    return Mark(stamp=stamp.strip(), name=name.strip() or "the board", quote=quote.strip())


def consumed_stamps(meta: Path) -> set[str]:
    """The stamps a turn has already been spent on. An unreadable ledger costs a repeat."""
    path = meta / LEDGER_NAME
    if not path.is_file():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("consumed", []))
    except (ValueError, OSError):
        return set()


def take_unread_marks(meta: Path) -> list[Mark]:
    """The marks nobody has answered yet, written down as consumed before they are handed out.

    The ledger is written first and the marks returned second, on purpose. A turn that dies
    halfway leaves the mark retired from the ledger's view and still in the inbox: the
    visible half of the failure, and the one a person can mend with `BoardRetireMarks`. The
    other order would answer the same mark on every look, forever.

    Two marks made in the same second share a stamp and the second reads as consumed. That
    is the inbox's own granularity — `retire_marks` also removes by stamp — so the ledger
    loses nothing the rest of the board does not already lose.
    """
    stamps = consumed_stamps(meta)
    unread = [mark for mark in read_inbox_marks(meta) if mark.stamp not in stamps]
    if unread:
        stamps.update(mark.stamp for mark in unread)
        write_ledger(meta, stamps)
    return unread


def write_ledger(meta: Path, stamps: set[str]) -> None:
    """Replace the ledger atomically: a reader never sees half a file."""
    meta.mkdir(parents=True, exist_ok=True)
    path = meta / LEDGER_NAME
    temporary = path.with_name(f"{LEDGER_NAME}.tmp")
    temporary.write_text(json.dumps({"consumed": sorted(stamps)}, indent=2), encoding="utf-8")
    os.replace(temporary, path)


async def board_nudge():
    """Yield one turn per unread mark, then look again after `NUDGE_EVERY` seconds.

    This is the shape `append_trigger` takes — a callable returning an async iterable —
    and returning before the first `yield` is what keeps the default silent.
    """
    if not config.nudge_enabled():
        return
    every = config.nudge_every()
    meta = config.meta_dir()
    while True:
        try:
            marks = take_unread_marks(meta)
        except OSError:
            marks = []  # no board folder yet is a wait, not a failure
        for mark in marks:
            yield mark.prompt()
        await asyncio.sleep(every)
