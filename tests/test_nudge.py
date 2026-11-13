"""The nudge: a mark becomes a turn, and the ledger keeps it from becoming two.

No zrb and no server here — the trigger only reads a file and a ledger, so it is tested the
way it runs: a temporary folder with an inbox in it, and a switch in the environment.
"""

import asyncio

import pytest

from board import config, nudge

MARK = "- 2026-11-08T12:44:56+07:00 — board: 0001-one — the words I selected"
BARE = "- 2026-11-08T12:44:56+07:00 — board: 0001-one"


@pytest.fixture
def meta(tmp_path):
    (tmp_path / "docs").mkdir(exist_ok=True)
    folder = tmp_path / ".board"
    folder.mkdir()
    return folder


def write_inbox(meta, *lines):
    (meta / nudge.INBOX_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_mark_is_read_into_its_document_its_stamp_and_its_passage(meta):
    write_inbox(meta, MARK, BARE)
    marks = nudge.read_inbox_marks(meta)
    assert [mark.name for mark in marks] == ["0001-one", "0001-one"]
    assert marks[0].stamp == "2026-11-08T12:44:56+07:00"
    assert marks[0].quote == "the words I selected"
    assert marks[1].quote == ""


def test_a_line_that_is_not_a_mark_is_skipped(meta):
    write_inbox(meta, "not a mark at all", "- 2026-11-08T12:44:56+07:00 — something else", MARK)
    assert [mark.quote for mark in nudge.read_inbox_marks(meta)] == ["the words I selected"]


def test_a_passage_asks_for_the_answer_beside_it(meta):
    write_inbox(meta, MARK)
    (prompt,) = [mark.prompt() for mark in nudge.take_unread_marks(meta)]
    assert 'BoardNote("0001-one", "the words I selected"' in prompt
    assert 'BoardRetireMarks(upto="2026-11-08T12:44:56+07:00")' in prompt
    assert "answer here in the chat" not in prompt


def test_no_passage_answers_in_the_chat_and_says_why(meta):
    write_inbox(meta, BARE)
    (prompt,) = [mark.prompt() for mark in nudge.take_unread_marks(meta)]
    assert "answer here in the chat" in prompt
    assert "BoardNote" not in prompt, "a mark with no quotation has nothing to anchor a note to"
    assert 'BoardRetireMarks(upto="2026-11-08T12:44:56+07:00")' in prompt


def test_the_ledger_stops_the_same_mark_from_being_answered_twice(meta):
    write_inbox(meta, MARK)
    assert len(nudge.take_unread_marks(meta)) == 1
    assert nudge.take_unread_marks(meta) == []


def test_a_restart_does_not_re_fire_what_was_already_taken(meta):
    # The ledger is on disk, so nothing here depends on a process staying alive.
    write_inbox(meta, MARK)
    nudge.take_unread_marks(meta)
    assert nudge.consumed_stamps(meta) == {"2026-11-08T12:44:56+07:00"}
    assert nudge.take_unread_marks(meta) == []


def test_a_new_mark_after_an_answered_one_is_still_taken(meta):
    write_inbox(meta, MARK)
    nudge.take_unread_marks(meta)
    write_inbox(meta, MARK, "- 2026-11-08T12:50:00+07:00 — board: 0002-two")
    assert [mark.name for mark in nudge.take_unread_marks(meta)] == ["0002-two"]


def test_a_mark_retired_by_hand_does_not_come_back(meta):
    write_inbox(meta, MARK)
    nudge.take_unread_marks(meta)
    write_inbox(meta)  # the inbox is empty: BoardRetireMarks removed the line
    assert nudge.take_unread_marks(meta) == []


def test_an_unreadable_ledger_costs_a_repeat_and_not_a_crash(meta):
    write_inbox(meta, MARK)
    (meta / nudge.LEDGER_NAME).write_text("{ not json", encoding="utf-8")
    assert len(nudge.take_unread_marks(meta)) == 1


def test_the_ledger_never_names_a_document(meta):
    write_inbox(meta, MARK)
    nudge.take_unread_marks(meta)
    written = (meta / nudge.LEDGER_NAME).read_text(encoding="utf-8")
    assert "0001-one" not in written, "the ledger holds stamps, not documents"


def test_the_switch_is_off_until_it_is_set(monkeypatch, meta):
    monkeypatch.delenv(config.env_name("NUDGE"), raising=False)
    monkeypatch.setenv(config.env_name("META"), str(meta))
    write_inbox(meta, MARK)

    async def collect():
        return [item async for item in nudge.board_nudge()]

    assert asyncio.run(collect()) == []
    assert nudge.take_unread_marks(meta) != [], "nothing was consumed while it was off"


def test_the_switch_is_read_every_look_not_at_import(monkeypatch, meta):
    monkeypatch.setenv(config.env_name("META"), str(meta))
    monkeypatch.setenv(config.env_name("NUDGE_EVERY"), "0")
    monkeypatch.setenv(config.env_name("NUDGE"), "1")
    write_inbox(meta, MARK)

    async def first_turn():
        trigger = nudge.board_nudge()
        try:
            return await trigger.__anext__()
        finally:
            await trigger.aclose()

    prompt = asyncio.run(first_turn())
    assert 'BoardNote("0001-one"' in prompt
