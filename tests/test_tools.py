"""The agent's tools: what is waiting, what a document says, and what a note takes.

And the `purpose` each of them accepts without reading, which is what keeps a call that
carries one -- every call a model makes -- from being refused as an extra argument.
"""

import sys
from pathlib import Path

import pytest
from pydantic_ai import Tool

from board import app, tools

# The seven, in the order they are registered, as `board/zrb_init.py` names them.
TOOLS = (
    tools.BoardState,
    tools.BoardRead,
    tools.BoardWrite,
    tools.BoardCreate,
    tools.BoardNote,
    tools.BoardResolveNote,
    tools.BoardRetireMarks,
)

# The least a call can send and be a real call, beside the `purpose` under test.
MINIMAL_CALLS = (
    (tools.BoardState, {}),
    (tools.BoardRead, {"name": "0001-q"}),
    (tools.BoardWrite, {"name": "0001-q", "markdown": "# Q\n", "rev": "abc"}),
    (tools.BoardCreate, {"name": "0013-new", "markdown": "# New\n"}),
    (tools.BoardNote, {"name": "0001-q", "quote": "First paragraph", "note": "a note"}),
    (tools.BoardResolveNote, {"name": "0001-q", "note_id": "a1"}),
    (tools.BoardRetireMarks, {}),
)


def test_ink_is_not_a_note():
    assert tools._is_note({"id": "a1", "kind": "note"})
    assert tools._is_note({"id": "a1"})  # an annotation older than `kind`
    assert not tools._is_note({"id": "a1", "kind": "ink", "strokes": [{}, {}]})


def test_marks_fold_per_document_and_keep_the_newest_time():
    inbox = [
        "- 2026-11-08T09:02:26+07:00 — board: 0001-one",
        "- 2026-11-08T09:04:34+07:00 — board: 0002-two",
        "- 2026-11-08T12:44:56+07:00 — board: 0001-one",
    ]
    assert tools._marks_waiting(inbox) == [
        "- 0001-one ×2 — newest 2026-11-08T12:44:56+07:00",
        "- 0002-two — 2026-11-08T09:04:34+07:00",
    ]
    assert tools._marks_waiting([]) == ["(no marks waiting)"]


def test_a_conflict_is_recognised_even_when_the_document_is_empty():
    # A stale write against an empty document must still come back as the merge response,
    # so the body's keys decide, not its values.
    assert tools._is_conflict({"rev": "abc", "markdown": ""})
    assert not tools._is_conflict({"detail": "no document named nope"})
    assert not tools._is_conflict("not a body")


def test_the_read_view_is_told_apart_from_the_document():
    view = (
        "# Q\n"
        "rev 8173f5fe4d34 · updated 2026-11-10T07:26 · 1 annotation(s)\n"
        "source: docs/0001-question.md\n\n# Q\n\ntext\n"
    )
    assert tools._is_the_read_view(view, "0001-question", "8173f5fe4d34")
    assert not tools._is_the_read_view("# Q\n\ntext\n", "0001-question", "8173f5fe4d34")
    # A page that quotes a digest of its own is a page: the revision it names is not this
    # document's, so the guard does not fire on it.
    assert not tools._is_the_read_view(view, "0001-question", "0000000000ff")
    assert not tools._is_the_read_view(view, "another-document", "8173f5fe4d34")


def test_the_source_view_is_the_document_itself(monkeypatch):
    # Nothing wrapped around it: what BoardRead(source=True) returns is byte for byte what
    # BoardWrite takes, so a caller cannot paste a wrapper back into the file.
    markdown = "# Q\n\ntext\n"
    monkeypatch.setattr(
        tools, "_json", lambda *args, **kwargs: {"rev": "abc", "markdown": markdown}
    )
    assert tools.BoardRead("0001-question", source=True) == markdown


def test_a_note_is_one_request_to_the_board(monkeypatch):
    # The tools do not read markdown and do not locate anything: the board does both, in
    # the request that stores the note, so no edit can slip between the two.
    sent = []

    def fake_json(method, path, payload=None):
        sent.append((method, path, payload))
        return {"id": "a1", "block": "b_1", "quote": "foo bar", "note": "the row"}

    monkeypatch.setattr(tools, "_json", fake_json)
    assert "b_1" in tools.BoardNote("0001-q", "| foo | bar |", "the row")
    assert sent == [
        (
            "POST",
            "/api/docs/0001-q/notes",
            {"quote": "| foo | bar |", "note": "the row", "author": tools.NOTE_AUTHOR},
        )
    ]


def test_a_refused_quote_reaches_the_caller_as_a_sentence(monkeypatch):
    def refuse(method, path, payload=None):
        raise RuntimeError("no block holds the whole quote")

    monkeypatch.setattr(tools, "_json", refuse)
    with pytest.raises(RuntimeError, match="no block holds"):
        tools.BoardNote("0001-q", "nowhere", "a note")


def test_the_default_address_follows_the_board_settings(monkeypatch):
    # The tools call the port the server binds, not one of their own.
    monkeypatch.setenv("ZRB_BOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("ZRB_BOARD_PORT", "9000")
    assert tools._default_url() == "http://127.0.0.1:9000"
    monkeypatch.setenv("ZRB_BOARD_HOST", "::1")
    assert tools._default_url() == "http://[::1]:9000"
    monkeypatch.delenv("ZRB_BOARD_HOST")
    monkeypatch.delenv("ZRB_BOARD_PORT")
    assert tools._default_url() == "http://127.0.0.1:8765"


def test_init_path_names_the_file_shipped_beside_the_server(monkeypatch, capsys):
    # `zrb-board init-path` is what a shell puts in ZRB_INIT_SCRIPTS; it must name a real
    # file, or zrb warns about an init source that is not there.
    monkeypatch.setattr(sys, "argv", ["zrb-board", "init-path"])
    app.main()
    printed = Path(capsys.readouterr().out.strip())
    assert printed.is_file() and printed.name == "zrb_init.py"


def test_every_tool_offers_the_purpose_a_model_always_sends():
    # zrb's own tools ask for a `purpose` on every call, so a model writes one on every
    # call, board tools included. The schema is what the model reads and what its
    # arguments are validated against, so the contract is checked there -- in the shape
    # pydantic-ai builds from the signature, not in the signature alone.
    for tool in TOOLS:
        properties = Tool(tool).function_schema.json_schema["properties"]
        assert properties["purpose"]["type"] == "string", tool.__name__
        # Optional: a model that says nothing is not punished either way, and a python
        # caller (a test, another tool) may leave it out.
        assert properties["purpose"]["default"] == "", tool.__name__


def test_a_call_that_says_its_purpose_is_not_refused():
    # The arguments of a call, put through the validator that runs before the body:
    # `extra_forbidden` on `purpose` was the whole defect, and it fired on every tool.
    for tool, arguments in MINIMAL_CALLS:
        validated = Tool(tool).function_schema.validator.validate_python(
            {**arguments, "purpose": "seeing what the board holds"}
        )
        assert validated["purpose"] == "seeing what the board holds", tool.__name__


def test_the_purpose_is_sent_nowhere(monkeypatch):
    # Accepted, then dropped: it is a line for the narrator, which reads it from the
    # call's arguments itself, and not an argument to any request the board receives.
    sent = []

    def record(method, path, payload=None):
        sent.append(payload)
        return {"id": "a1", "block": "b_1"}

    monkeypatch.setattr(tools, "_json", record)
    tools.BoardNote("0001-q", "foo", "the row", purpose="asking about foo")
    assert sent == [
        {"quote": "foo", "note": "the row", "author": tools.NOTE_AUTHOR},
    ]


def test_a_new_document_is_one_request_to_the_board(monkeypatch):
    sent = []

    def fake_json(method, path, payload=None):
        sent.append((method, path, payload))
        return {"rev": "abc", "updated": "2026-11-13T12:00:00"}

    monkeypatch.setattr(tools, "_json", fake_json)
    assert "abc" in tools.BoardCreate("0013-new", "# New\n\ntext\n")
    assert sent == [
        ("POST", "/api/docs", {"name": "0013-new", "markdown": "# New\n\ntext\n"})
    ]


def test_a_document_with_no_text_is_not_created(monkeypatch):
    monkeypatch.setattr(tools, "_json", lambda *args, **kwargs: pytest.fail("called"))
    with pytest.raises(RuntimeError, match="nothing created"):
        tools.BoardCreate("0013-new", "  \n")


def test_retiring_marks_says_how_many_went(monkeypatch):
    sent = []

    def fake_json(method, path, payload=None):
        sent.append((method, path, payload))
        return {"retired": 3}

    monkeypatch.setattr(tools, "_json", fake_json)
    # One is "1 mark"; three is "3 marks". The count is the whole answer to the call.
    assert "Retired 3 marks" in tools.BoardRetireMarks("2026-11-08T12:44:56+07:00")
    assert sent == [
        ("POST", "/api/inbox/retire", {"upto": "2026-11-08T12:44:56+07:00"})
    ]
