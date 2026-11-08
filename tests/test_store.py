import pytest

from board.store import (
    DocExists,
    DocNotFound,
    InvalidName,
    RevConflict,
    content_rev,
)


def test_should_advance_the_revision_when_the_text_changes(store):
    doc = store.create("0001-question", "# Q\n\ntext\n")
    updated = store.write("0001-question", "# Q\n\ntext changed\n", doc.rev)
    assert updated.rev != doc.rev
    assert updated.rev == content_rev(updated.markdown)


def test_should_refuse_a_stale_write_and_hand_back_the_current_text(store):
    doc = store.create("0001-question", "# Q\n\ntext\n")
    with pytest.raises(RevConflict) as caught:
        store.write("0001-question", "# Q\n\nclobbered\n", "000000000000")
    assert caught.value.markdown == "# Q\n\ntext\n"
    assert store.read("0001-question").markdown == "# Q\n\ntext\n"


def test_should_refuse_names_that_could_leave_the_docs_directory(store):
    for name in ["../etc/passwd", "a/b", "", ".hidden", "note.md"]:
        with pytest.raises(InvalidName):
            store.read(name)


def test_should_refuse_to_create_a_document_twice(store):
    store.create("0001-question", "# Q\n")
    with pytest.raises(DocExists):
        store.create("0001-question", "# Q\n")


def test_should_number_annotations_in_order(store):
    store.create("0001-question", "# Q\n\ntext\n")
    first = store.add_annotation("0001-question", _note("text"))
    second = store.add_annotation("0001-question", _note("text"))
    assert (first["id"], second["id"]) == ("a1", "a2")
    assert [note["id"] for note in store.annotations("0001-question")] == ["a1", "a2"]


def test_should_refuse_an_annotation_on_a_document_that_is_not_there(store):
    with pytest.raises(DocNotFound):
        store.add_annotation("missing", _note("text"))


def test_should_mark_an_annotation_resolved(store):
    store.create("0001-question", "# Q\n\ntext\n")
    note = store.add_annotation("0001-question", _note("text"))
    assert store.set_resolved("0001-question", note["id"], True)["resolved"] is True


def test_should_leave_the_title_as_the_name_when_there_is_no_heading(store):
    doc = store.create("0001-question", "no heading here\n")
    assert doc.title == "0001-question"


def test_should_keep_one_ink_annotation_per_block(store):
    store.create("0001-question", "# Q\n\ntext\n")
    first = store.set_ink("0001-question", "b_1", "text", [_stroke(0.0)])
    second = store.set_ink("0001-question", "b_1", "text", [_stroke(0.0), _stroke(0.5)])
    assert first["id"] == second["id"]
    assert len(store.annotations("0001-question")) == 1
    assert len(second["strokes"]) == 2


def test_should_keep_ink_apart_from_a_written_note(store):
    store.create("0001-question", "# Q\n\ntext\n")
    store.add_annotation("0001-question", _note("text"))
    store.set_ink("0001-question", "b_1", "text", [_stroke(0.0)])
    kinds = [annotation["kind"] for annotation in store.annotations("0001-question")]
    assert kinds == ["note", "ink"]


def test_should_refuse_ink_on_a_document_that_is_not_there(store):
    with pytest.raises(DocNotFound):
        store.set_ink("missing", "b_1", "text", [_stroke(0.0)])


def test_should_drop_the_ink_note_when_its_last_stroke_is_erased(store):
    store.create("0001-question", "# Q\n\ntext\n")
    store.set_ink("0001-question", "b_1", "text", [_stroke(0.0)])
    store.set_ink("0001-question", "b_1", "text", [])
    assert store.annotations("0001-question") == []


def test_should_not_create_an_ink_note_for_an_empty_erase(store):
    store.create("0001-question", "# Q\n\ntext\n")
    store.set_ink("0001-question", "b_1", "text", [])
    assert store.annotations("0001-question") == []


def test_should_let_each_author_keep_their_own_ink_on_one_block(store):
    store.create("0001-question", "# Q\n\ntext\n")
    store.set_ink("0001-question", "b_1", "text", [_stroke(0.0)], "go")
    store.set_ink("0001-question", "b_1", "text", [_stroke(0.5)], "tara")
    inks = [item for item in store.annotations("0001-question") if item["kind"] == "ink"]
    assert sorted(item["author"] for item in inks) == ["go", "tara"]
    assert all(len(item["strokes"]) == 1 for item in inks)


def test_should_erase_only_the_erasing_author_s_ink(store):
    store.create("0001-question", "# Q\n\ntext\n")
    store.set_ink("0001-question", "b_1", "text", [_stroke(0.0)], "go")
    store.set_ink("0001-question", "b_1", "text", [_stroke(0.5)], "tara")
    store.set_ink("0001-question", "b_1", "text", [], "go")
    inks = [item for item in store.annotations("0001-question") if item["kind"] == "ink"]
    assert [item["author"] for item in inks] == ["tara"]


def test_should_remove_an_annotation_and_leave_the_others(store):
    store.create("0001-question", "# Q\n\ntext\n")
    first = store.add_annotation("0001-question", _note("text"))
    store.add_annotation("0001-question", _note("Q"))
    assert store.remove_annotation("0001-question", first["id"]) == {
        "id": first["id"],
        "removed": True,
    }
    remaining = store.annotations("0001-question")
    assert [item["id"] for item in remaining] == ["a2"]


def test_should_refuse_to_remove_an_annotation_that_is_not_there(store):
    store.create("0001-question", "# Q\n\ntext\n")
    with pytest.raises(DocNotFound):
        store.remove_annotation("0001-question", "a9")


def _stroke(offset: float) -> dict:
    return {"points": [{"x": offset, "y": 0.0}, {"x": offset + 0.1, "y": 0.2}], "color": "#c0392b"}


def _note(quote: str) -> dict:
    return {
        "block": "b_1",
        "quote": quote,
        "start": 0,
        "end": len(quote),
        "note": "a note",
        "author": "go",
    }
