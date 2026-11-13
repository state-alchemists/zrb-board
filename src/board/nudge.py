"""A mark becomes a turn, and the answer lands in the margin beside what was marked.

`board/zrb_init.py` registers `board_nudge` on zrb's chat task, so a passage Go marks in
the browser is answered without anyone typing first. A trigger is a callable returning an
async iterable, and every item it yields is a user turn (`zrb/llm/ui/trigger.py:1-11`).

Six rules this file exists to keep:

- **Off unless switched on.** A mark may start a turn only while `ZRB_BOARD_NUDGE` is set.
  The switch is read at every look, but the trigger is registered once, when the chat
  starts, so switching it on is a new chat and switching it off is the next look.
- **Marking has to have stopped.** A turn starts only once the newest mark is `NUDGE_SETTLE`
  seconds old — thirty by default. Somebody still adding marks has not finished the ones
  already in the queue, and an answer that arrives while the next selection is being dragged
  is an interruption rather than help. Measured from the newest mark rather than from the one
  about to be answered, so a run of marks is answered after the run.
- **One mark, one turn, claimed one at a time.** A ledger of claimed ids sits beside the
  inbox, and an id is written into it immediately before that mark is yielded — never for a
  whole batch. Claiming the inbox up front would lose every mark after the first if the
  chat stopped between turns: still in the inbox, already counted as answered.
- **A claim is a lease, not a tombstone.** The claim carries the time it was made, and one
  older than `CLAIM_TTL_SECONDS` stops counting. A claim written for a turn that never ran
  — the chat was killed between the claim and the yield — would otherwise hide that mark
  from every later chat: still in the inbox, and skipped for ever.
- **One chat claims at a time.** Reading the inbox and writing the ledger is a
  read-modify-write, and two chats on one board are two trigger loops: without a lock both
  see the same mark unclaimed and both answer it. The claim runs under `daemon.only_one`,
  the same advisory file lock that stops two `-d` starting two servers, and a chat that
  finds it held takes the next look instead of waiting.
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
from datetime import datetime, timedelta
from pathlib import Path

from board import config, daemon, store

INBOX_NAME = "inbox.md"
LEDGER_NAME = "nudge.json"
LEDGER_LOCK_NAME = "nudge.lock"
# Ten minutes: longer than any turn this trigger starts, and short enough that a chat killed
# mid-turn costs a repeat rather than a mark.
CLAIM_TTL_SECONDS = 600.0


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

        The arguments are JSON strings, not the passage pasted between quote marks: a
        selected passage can contain `"` all by itself, and `BoardNote("doc", "He said
        "no"")` is not a call anybody can read — least of all the agent it is addressed to.
        """
        retire = f"BoardRetireMarks(id={json.dumps(self.id)})"
        name = json.dumps(self.name)
        if not self.quote:
            return (
                f"board: {self.name} is marked, with no passage chosen. "
                f"Read it with BoardRead({name}), answer here in the chat — there is no "
                f"quotation to anchor a note to — and then retire this mark: {retire}."
            )
        return (
            f"board: {self.name} is marked, on this passage:\n\n"
            f"> {self.quote}\n\n"
            f"Read the document, answer that passage in the margin with "
            f"BoardNote({name}, {json.dumps(self.quote)}, your answer) — the quotation is "
            f"what anchors it — and then retire this mark: {retire}. If that passage can no "
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


def _now() -> datetime:
    """The clock claims are compared against, aware like the stamps the inbox writes."""
    return datetime.now().astimezone()


def _made_at(stamp: str | None) -> datetime | None:
    """When a stamp says something happened, or `None` when nothing can read it."""
    if not stamp:
        return None
    try:
        made = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return made if made.tzinfo else made.astimezone()


def quiet_for(meta: Path) -> float:
    """Seconds since the newest mark was made — infinite when the inbox holds none.

    From the newest, not from the oldest unclaimed: somebody adding a third mark has not
    finished the first two, so the whole queue waits for them. A stamp nothing can read is
    left out rather than holding the queue for ever.
    """
    newest = None
    for mark in read_inbox_marks(meta):
        made = _made_at(mark.stamp)
        if made is not None and (newest is None or made > newest):
            newest = made
    if newest is None:
        return float("inf")
    return (_now() - newest).total_seconds()


def _ledger(meta: Path) -> dict[str, datetime]:
    """The ids a turn has been spent on, and when. An unreadable ledger costs a repeat."""
    path = meta / LEDGER_NAME
    if not path.is_file():
        return {}
    try:
        claims = json.loads(path.read_text(encoding="utf-8")).get("claimed", {})
    except (ValueError, OSError, AttributeError):
        return {}
    if isinstance(claims, list):
        # Written before a claim carried its time. Trust it once: the next write dates it.
        claims = dict.fromkeys(claims)
    return {identity: _made_at(when) or _now() for identity, when in claims.items()}


def _live_claims(meta: Path) -> dict[str, datetime]:
    """Claims still worth keeping: not expired, and about a line still in the inbox.

    Dropping the retired ones keeps the ledger the size of the queue rather than the size of
    every mark ever made — and it is what lets a mark identical to a retired one be answered
    later instead of being skipped by an id left over from the one before it.
    """
    inbox = {mark.id for mark in read_inbox_marks(meta)}
    now = _now()
    lease = timedelta(seconds=CLAIM_TTL_SECONDS)
    return {
        identity: when
        for identity, when in _ledger(meta).items()
        if identity in inbox and now - when < lease
    }


def claimed_ids(meta: Path) -> set[str]:
    """The marks a turn is live on: claimed, and not older than the lease."""
    return set(_live_claims(meta))


def next_unread_mark(meta: Path, settle: float | None = None) -> Mark | None:
    """The oldest mark nobody is answering, claimed in the ledger before it is handed over.

    Three refusals before anything is taken:

    - **marking has to have stopped** — nothing is claimed while the newest mark is younger
      than *settle* seconds, which defaults to `config.nudge_settle()`. A selection made a
      moment ago may be one of three, and answering the first while the second is being
      dragged is the interruption this feature is supposed to save somebody from.
    - **another chat must not be claiming** — the ledger is read, decided on, and written
      back, so two trigger loops that did that at once would both see the same mark
      unclaimed. The whole claim is held under one file lock, and a chat that finds it taken
      looks again rather than waiting: the other chat is doing the same job.
    - **a claim has to be live to count** — a claim is a lease and expires, so a chat killed
      between claiming a mark and running its turn leaves the mark answerable.

    Two lines with identical words share an id, and therefore count as one mark. That is the
    one thing the words cannot tell apart — and `store.notify` refuses to write the same mark
    twice inside a couple of seconds, so the queue does not hold two of them.
    """
    settle = config.nudge_settle() if settle is None else settle
    if quiet_for(meta) < settle:
        return None
    with daemon.only_one(meta / LEDGER_LOCK_NAME) as mine:
        if not mine:
            return None  # another chat is claiming; this one looks again next time
        claims = _live_claims(meta)
        for mark in read_inbox_marks(meta):
            if mark.id in claims:
                continue
            claims[mark.id] = _now()
            write_ledger(meta, claims)
            return mark
    return None


def _temporary(path: Path) -> Path:
    """A scratch name of this process's own: two chats must not write one temporary file."""
    return path.with_name(f"{path.name}.{os.getpid()}.tmp")


def write_ledger(meta: Path, claims: dict[str, datetime]) -> None:
    """Replace the ledger atomically: a reader never sees half a file."""
    meta.mkdir(parents=True, exist_ok=True)
    path = meta / LEDGER_NAME
    temporary = _temporary(path)
    payload = {"claimed": {identity: when.isoformat() for identity, when in sorted(claims.items())}}
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)


async def board_nudge():
    """Yield one turn per unclaimed mark, then look again after `NUDGE_EVERY` seconds.

    This is the shape `append_trigger` takes — a callable returning an async iterable. The
    switch is read before every look, not once at the start, so turning the nudge off takes
    effect at the next look rather than at the next chat; while it is off this generator
    simply yields nothing and keeps sleeping.
    """
    while True:
        if config.nudge_enabled():
            for mark in _claim_in_order(config.meta_dir()):
                yield mark.prompt()
        await asyncio.sleep(config.nudge_every())


def _claim_in_order(meta: Path, enabled=config.nudge_enabled, settle: float | None = None):
    """Claim marks one at a time, asking *enabled* before each, until the inbox is empty.

    A generator of its own so the claim for each mark happens as that mark is reached —
    after the turn before it has finished, and never for marks that are only queued — and
    so the switch can stop the queue between two marks rather than after all of them.
    `next_unread_mark` holds the same queue while marking is still going on.
    """
    while enabled():
        try:
            mark = next_unread_mark(meta, settle)
        except OSError:
            return  # no board folder yet is a wait, not a failure
        if mark is None:
            return
        if mark.id not in {other.id for other in read_inbox_marks(meta)}:
            # Retired between the claim and this line: `BoardRetireMarks` can land in that
            # gap, and a turn for a mark somebody has just cleared would answer work they
            # explicitly took back. The claim dies with the line, so nothing is owed.
            return
        yield mark
