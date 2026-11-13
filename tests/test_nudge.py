"""The nudge: a mark becomes a turn, and the ledger keeps it from becoming two.

No zrb and no server here — the trigger only reads a file and a ledger, so it is tested the
way it runs: a temporary folder with an inbox in it, and a switch in the environment.

The two things these tests are most careful about are the two ways a queue quietly loses
work: claiming a whole inbox that only half got answered, and treating a stamp — good to
the second — as if it named one mark.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta

import pytest

from board import config, daemon, nudge, store

MARK = "- 2026-11-08T12:44:56+07:00 — board: 0001-one — the words I selected"
OTHER = "- 2026-11-08T12:44:56+07:00 — board: 0001-one — a second passage, same second"
BARE = "- 2026-11-08T12:44:56+07:00 — board: 0001-one"


def mark_made_now(quote: str, name: str = "0001-one") -> str:
    """A mark with this second on it, which is what the board writes when you click."""
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    return f"- {stamp} — board: {name} — {quote}"


@pytest.fixture
def meta(tmp_path):
    (tmp_path / "docs").mkdir(exist_ok=True)
    folder = tmp_path / ".board"
    folder.mkdir()
    return folder


def write_inbox(meta, *lines):
    (meta / nudge.INBOX_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_mark_is_read_into_its_id_its_document_its_stamp_and_its_passage(meta):
    write_inbox(meta, MARK, BARE)
    marks = nudge.read_inbox_marks(meta)
    assert [mark.name for mark in marks] == ["0001-one", "0001-one"]
    assert marks[0].stamp == "2026-11-08T12:44:56+07:00"
    assert marks[0].quote == "the words I selected"
    assert marks[1].quote == ""
    assert marks[0].id == store.mark_id(MARK)
    assert marks[0].id != marks[1].id


def test_a_line_that_is_not_a_mark_is_skipped(meta):
    write_inbox(meta, "not a mark at all", "- 2026-11-08T12:44:56+07:00 — something else", MARK)
    assert [mark.quote for mark in nudge.read_inbox_marks(meta)] == ["the words I selected"]


def test_a_passage_asks_for_the_answer_beside_it(meta):
    write_inbox(meta, MARK)
    mark = nudge.next_unread_mark(meta)
    prompt = mark.prompt()
    assert 'BoardNote("0001-one", "the words I selected"' in prompt
    assert f'BoardRetireMarks(id="{mark.id}")' in prompt
    assert "answer here in the chat" not in prompt


def test_no_passage_answers_in_the_chat_and_says_why(meta):
    write_inbox(meta, BARE)
    prompt = nudge.next_unread_mark(meta).prompt()
    assert "answer here in the chat" in prompt
    assert "BoardNote" not in prompt, "a mark with no quotation has nothing to anchor a note to"


def test_marks_are_claimed_one_at_a_time(meta):
    write_inbox(meta, MARK, OTHER)
    first = nudge.next_unread_mark(meta)
    second = nudge.next_unread_mark(meta)
    assert first.quote == "the words I selected"
    assert second.quote == "a second passage, same second"
    assert nudge.next_unread_mark(meta) is None


def test_two_marks_in_one_second_are_two_marks_not_one(meta):
    # The reviewer's case: the stamp is good to the second, so the stamp cannot be the id.
    write_inbox(meta, MARK, OTHER)
    marks = nudge.read_inbox_marks(meta)
    assert marks[0].stamp == marks[1].stamp
    assert marks[0].id != marks[1].id
    nudge.next_unread_mark(meta)
    assert nudge.next_unread_mark(meta) is not None, "the second mark must still get a turn"


def test_two_lines_with_the_same_words_are_one_mark(meta):
    write_inbox(meta, MARK, MARK)
    assert len(nudge.read_inbox_marks(meta)) == 2
    assert nudge.next_unread_mark(meta) is not None
    assert nudge.next_unread_mark(meta) is None, "identical words are one intention"


def test_the_ledger_holds_ids_and_not_stamps(meta):
    write_inbox(meta, MARK)
    mark = nudge.next_unread_mark(meta)
    written = (meta / nudge.LEDGER_NAME).read_text(encoding="utf-8")
    assert mark.id in written
    assert mark.stamp not in written, "a stamp belongs to two marks at once; an id does not"


def test_a_restart_does_not_re_fire_what_was_already_claimed(meta):
    # The ledger is on disk, so nothing here depends on a process staying alive.
    write_inbox(meta, MARK)
    nudge.next_unread_mark(meta)
    assert nudge.claimed_ids(meta) == {store.mark_id(MARK)}
    assert nudge.next_unread_mark(meta) is None


def test_a_chat_that_stops_after_one_turn_does_not_lose_the_second(meta):
    # The claim happens as each mark is reached, not for the whole inbox up front, so a
    # process that dies between turns leaves the rest of the queue answerable.
    write_inbox(meta, MARK, OTHER)
    marks = nudge._claim_in_order(meta, enabled=lambda: True)
    first = next(marks)
    marks.close()
    assert first.quote == "the words I selected"
    assert nudge.claimed_ids(meta) == {store.mark_id(MARK)}
    assert nudge.next_unread_mark(meta).quote == "a second passage, same second"


def test_a_new_mark_after_an_answered_one_is_still_taken(meta):
    write_inbox(meta, MARK)
    nudge.next_unread_mark(meta)
    write_inbox(meta, MARK, "- 2026-11-08T12:50:00+07:00 — board: 0002-two")
    assert nudge.next_unread_mark(meta).name == "0002-two"


def test_a_mark_retired_by_hand_does_not_come_back(meta):
    write_inbox(meta, MARK)
    nudge.next_unread_mark(meta)
    write_inbox(meta)  # the inbox is empty: BoardRetireMarks removed the line
    assert nudge.next_unread_mark(meta) is None


def test_an_unreadable_ledger_costs_a_repeat_and_not_a_crash(meta):
    write_inbox(meta, MARK)
    (meta / nudge.LEDGER_NAME).write_text("{ not json", encoding="utf-8")
    assert nudge.next_unread_mark(meta) is not None


def test_the_switch_is_consulted_before_every_mark(meta):
    # Turning it off stops the queue between two marks, not after all of them.
    write_inbox(meta, MARK, OTHER)
    asked = []

    def enabled():
        asked.append(True)
        return len(asked) == 1  # the first mark only

    marks = list(nudge._claim_in_order(meta, enabled=enabled))
    assert len(marks) == 1
    assert len(asked) == 2, "asked again before the second mark, and refused"
    assert nudge.next_unread_mark(meta).quote == "a second passage, same second"


def test_a_passage_with_quotes_in_it_is_still_one_argument(meta):
    # `He said "no"` pasted between quote marks produces a call nobody can read.
    passage = 'He said "no" and left'
    write_inbox(meta, f"- 2026-11-08T12:44:56+07:00 — board: 0001-one — {passage}")
    prompt = nudge.next_unread_mark(meta).prompt()
    arguments = prompt.split("BoardNote(")[1].split(", your answer)")[0]
    name, quote = json.loads(f"[{arguments}]")
    assert (name, quote) == ("0001-one", passage)


def test_the_switch_is_off_until_it_is_set(monkeypatch, meta):
    monkeypatch.delenv(config.env_name("NUDGE"), raising=False)
    monkeypatch.setenv(config.env_name("META"), str(meta))
    monkeypatch.setenv(config.env_name("NUDGE_EVERY"), "0.01")
    write_inbox(meta, MARK)

    async def nothing_comes():
        # The trigger stays alive while it is off — that is what lets it be switched on
        # mid-chat — so "silent" is a wait with nothing in it, not an end.
        trigger = nudge.board_nudge()
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(trigger.__anext__(), timeout=0.2)
        finally:
            await trigger.aclose()

    asyncio.run(nothing_comes())
    assert nudge.claimed_ids(meta) == set(), "nothing is claimed while it is off"


def test_the_trigger_yields_each_mark_once_and_then_waits(monkeypatch, meta):
    monkeypatch.setenv(config.env_name("META"), str(meta))
    monkeypatch.setenv(config.env_name("NUDGE_EVERY"), "0")
    monkeypatch.setenv(config.env_name("NUDGE"), "1")
    write_inbox(meta, MARK, OTHER)

    async def two_turns():
        trigger = nudge.board_nudge()
        try:
            return [await trigger.__anext__(), await trigger.__anext__()]
        finally:
            await trigger.aclose()

    first, second = asyncio.run(two_turns())
    assert 'BoardNote("0001-one", "the words I selected"' in first
    assert 'BoardNote("0001-one", "a second passage, same second"' in second


def test_a_fresh_mark_waits_for_marking_to_stop(meta):
    # A mark made a second ago may be the first of three. Answering it now would start a
    # turn while the next selection is still being dragged.
    write_inbox(meta, mark_made_now("the words I selected"))
    assert nudge.quiet_for(meta) < config.DEFAULT_NUDGE_SETTLE
    assert nudge.next_unread_mark(meta) is None
    assert nudge.claimed_ids(meta) == set(), "nothing is claimed while marking is going on"


def test_the_wait_is_counted_from_the_newest_mark_not_the_one_being_answered(meta):
    write_inbox(meta, MARK, mark_made_now("a third passage"))
    assert nudge.next_unread_mark(meta) is None, "the old mark waits for the new one"


def test_an_old_mark_is_answered_without_a_wait(meta):
    write_inbox(meta, MARK)
    assert nudge.quiet_for(meta) > config.DEFAULT_NUDGE_SETTLE
    assert nudge.next_unread_mark(meta) is not None


def test_a_new_mark_pauses_the_queue_between_two_turns(meta):
    write_inbox(meta, MARK, OTHER)
    marks = nudge._claim_in_order(meta, enabled=lambda: True)
    assert next(marks).quote == "the words I selected"
    write_inbox(meta, MARK, OTHER, mark_made_now("marked while the first was being answered"))
    assert list(marks) == [], "the queue stops at a mark made mid-answer"
    assert nudge.claimed_ids(meta) == {store.mark_id(MARK)}, "and leaves the second unclaimed"


def test_the_wait_can_be_turned_off(monkeypatch, meta):
    monkeypatch.setenv(config.env_name("NUDGE_SETTLE"), "0")
    write_inbox(meta, mark_made_now("the words I selected"))
    assert nudge.next_unread_mark(meta) is not None


def test_a_stamp_nothing_can_read_does_not_hold_the_queue(meta):
    write_inbox(meta, "- not-a-time — board: 0001-one — the words I selected")
    assert nudge.quiet_for(meta) == float("inf")
    assert nudge.next_unread_mark(meta) is not None


def test_a_claim_that_was_never_answered_expires(meta):
    # The chat was killed between claiming the mark and running its turn. Without a lease
    # the mark would sit in the inbox, claimed, and be skipped for ever.
    write_inbox(meta, MARK)
    nudge.next_unread_mark(meta)
    expired = datetime.now().astimezone() - timedelta(seconds=nudge.CLAIM_TTL_SECONDS + 1)
    nudge.write_ledger(meta, {store.mark_id(MARK): expired})
    assert nudge.claimed_ids(meta) == set()
    assert nudge.next_unread_mark(meta) is not None, "the mark is retried, not lost"


def test_a_live_claim_is_not_answered_twice(meta):
    write_inbox(meta, MARK)
    nudge.next_unread_mark(meta)
    assert nudge.claimed_ids(meta) == {store.mark_id(MARK)}
    assert nudge.next_unread_mark(meta) is None


def test_the_ledger_forgets_a_mark_it_retired(meta):
    # Otherwise a mark identical to the retired one inherits its id and is skipped.
    write_inbox(meta, MARK)
    nudge.next_unread_mark(meta)
    write_inbox(meta, MARK.replace("2026-11-08", "2026-11-09"))
    assert nudge.next_unread_mark(meta) is not None
    written = json.loads((meta / nudge.LEDGER_NAME).read_text(encoding="utf-8"))
    assert len(written["claimed"]) == 1, "the ledger is the size of the queue, not of history"


def test_a_second_chat_cannot_claim_what_the_first_is_claiming(meta):
    # Reading the ledger and writing it back is not atomic on its own: two trigger loops
    # would both see this mark unclaimed and answer it twice.
    write_inbox(meta, MARK)
    with daemon.only_one(meta / nudge.LEDGER_LOCK_NAME) as mine:
        assert mine
        assert nudge.next_unread_mark(meta) is None, "the other chat holds the claim"
    assert nudge.next_unread_mark(meta) is not None, "and it is claimable once that chat is done"


def test_the_ledger_is_written_under_a_name_only_this_chat_uses(meta):
    # One shared `.board/nudge.json.tmp` would let two chats write the same file at once, or
    # replace it from under each other.
    temporary = nudge._temporary(meta / nudge.LEDGER_NAME)
    assert str(os.getpid()) in temporary.name
