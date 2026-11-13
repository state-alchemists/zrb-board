"""A mark becomes a turn, and the answer lands in the margin beside what was marked.

`board/zrb_init.py` registers `board_nudge` on zrb's chat task, so a passage Go marks in
the browser is answered without anyone typing first. A trigger is a callable returning an
async iterable, and every item it yields is a user turn (`zrb/llm/ui/trigger.py:1-11`).

Four rules this file exists to keep:

- **Off unless switched on.** A mark may start a turn only while `ZRB_BOARD_NUDGE` is set.
  The switch is read at every look, but the trigger is registered once, when the chat
  starts, so switching it on is a new chat and switching it off is the next look.
- **One mark, one turn, claimed one at a time.** A ledger of claimed ids sits beside the
  inbox, and an id is written into it immediately before that mark is yielded — never for a
  whole batch. Claiming the inbox up front would lose every mark after the first if the
  chat stopped between turns: still in the inbox, already counted as answered.
- **A stamp is not an identity.** Stamps are good to the second, so two marks made together
  share one. The id is `store.mark_id(line)` — a hash of the mark's own words — so two
  passages marked in the same second stay two marks, and retiring one leaves the other.
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

from board import config, store

INBOX_NAME = "inbox.md"
LEDGER_NAME = "nudge.json"


@dataclass(frozen=True)
class Mark:
    """One inbox line read: which entry it is, when, which document, and what passage."""

    id: str
    stamp: str
    name: str
    quote: str = ""

    def prompt(self) -> str:
        """The turn this mark is worth, in words an agent can act on.

        The passage is what makes the answer a note rather than a sentence: `BoardNote`
        anchors by quotation, so a mark that carries none is answered in the chat and says
        so, instead of the turn inventing a quote to hang an answer on. Retirement names
        the id rather than a stamp, because a stamp can belong to two marks at once.
        """
        retire = f'BoardRetireMarks(id="{self.id}")'
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
    return Mark(
        id=store.mark_id(line),
        stamp=stamp.strip(),
        name=name.strip() or "the board",
        quote=quote.strip(),
    )


def claimed_ids(meta: Path) -> set[str]:
    """The marks a turn has already been spent on. An unreadable ledger costs a repeat."""
    path = meta / LEDGER_NAME
    if not path.is_file():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("claimed", []))
    except (ValueError, OSError):
        return set()


def next_unread_mark(meta: Path) -> Mark | None:
    """The oldest mark nobody has answered, claimed in the ledger before it is handed over.

    One at a time, on purpose. The ledger entry goes in immediately before this mark's own
    turn is yielded, so a chat that stops between turns loses nothing: every mark after this
    one is still unclaimed, and this one is still visible in the inbox to be mended by hand.

    Two lines with identical words share an id, and therefore count as one mark. That is the
    one thing the words cannot tell apart — and two identical marks are one intention.
    """
    claimed = claimed_ids(meta)
    for mark in read_inbox_marks(meta):
        if mark.id in claimed:
            continue
        claimed.add(mark.id)
        write_ledger(meta, claimed)
        return mark
    return None


def write_ledger(meta: Path, claimed: set[str]) -> None:
    """Replace the ledger atomically: a reader never sees half a file."""
    meta.mkdir(parents=True, exist_ok=True)
    path = meta / LEDGER_NAME
    temporary = path.with_name(f"{LEDGER_NAME}.tmp")
    temporary.write_text(json.dumps({"claimed": sorted(claimed)}, indent=2), encoding="utf-8")
    os.replace(temporary, path)


async def board_nudge():
    """Yield one turn per unclaimed mark, then look again after `NUDGE_EVERY` seconds.

    This is the shape `append_trigger` takes — a callable returning an async iterable —
    and returning before the first `yield` is what keeps the default silent.
    """
    if not config.nudge_enabled():
        return
    every = config.nudge_every()
    meta = config.meta_dir()
    while True:
        for mark in _claim_in_order(meta):
            yield mark.prompt()
        await asyncio.sleep(every)


def _claim_in_order(meta: Path):
    """Claim marks one at a time until the inbox has none left, then stop.

    A generator of its own so the claim for each mark happens as that mark is reached —
    after the turn before it has finished, and never for marks that are only queued.
    """
    while True:
        try:
            mark = next_unread_mark(meta)
        except OSError:
            return  # no board folder yet is a wait, not a failure
        if mark is None:
            return
        yield mark
