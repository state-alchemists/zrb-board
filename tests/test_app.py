def _note(client, name, quote="text", note="a note"):
    return client.post(
        f"/api/docs/{name}/annotations",
        json={"block": "b_1", "quote": quote, "note": note, "author": "go"},
    )


def test_should_serve_a_document_with_every_block(client, store):
    store.create("0001-question", "# Q\n\ntext\n")
    payload = client.get("/api/docs/0001-question").json()
    assert payload["title"] == "Q"
    assert [block["text"] for block in payload["blocks"]] == ["Q", "text"]


def test_should_resolve_an_annotation_to_the_block_holding_its_quote(client, store):
    store.create("0001-question", "# Q\n\ntext\n")
    _note(client, "0001-question")
    payload = client.get("/api/docs/0001-question").json()
    annotation = payload["annotations"][0]
    assert annotation["orphaned"] is False
    assert annotation["resolved_block"] == payload["blocks"][1]["id"]


def test_should_orphan_an_annotation_whose_text_the_edit_removed(client, store):
    doc = store.create("0001-question", "# Q\n\ntext\n")
    _note(client, "0001-question")
    client.put(
        "/api/docs/0001-question",
        json={"markdown": "# Q\n\nsomething else\n", "rev": doc.rev},
    )
    payload = client.get("/api/docs/0001-question").json()
    assert payload["annotations"][0]["orphaned"] is True


def test_should_refuse_a_stale_write_with_409_and_the_current_text(client, store):
    store.create("0001-question", "# Q\n\ntext\n")
    response = client.put(
        "/api/docs/0001-question",
        json={"markdown": "# Q\n\nclobbered\n", "rev": "000000000000"},
    )
    assert response.status_code == 409
    assert response.json()["markdown"] == "# Q\n\ntext\n"


def test_should_accept_a_write_that_names_the_current_revision(client, store):
    doc = store.create("0001-question", "# Q\n\ntext\n")
    response = client.put(
        "/api/docs/0001-question",
        json={"markdown": "# Q\n\ntext, revised\n", "rev": doc.rev},
    )
    assert response.status_code == 200
    assert response.json()["rev"] != doc.rev


def test_should_serve_the_digest_as_plain_markdown(client, store):
    store.create("0001-question", "# Q\n\ntext\n")
    _note(client, "0001-question")
    response = client.get("/api/docs/0001-question/state.md")
    assert response.headers["content-type"].startswith("text/plain")
    assert "⟦a1 · go" in response.text


def test_should_answer_404_for_a_document_that_is_not_there(client):
    assert client.get("/api/docs/missing").status_code == 404


def test_should_record_a_marker_in_the_inbox(client):
    client.post("/api/inbox", json={"text": "board: 0001-question"})
    assert "board: 0001-question" in client.get("/api/inbox").text


def test_should_store_ink_against_the_block_it_was_drawn_on(client, store):
    store.create("0001-question", "# Q\n\ntext\n")
    block = client.get("/api/docs/0001-question").json()["blocks"][1]["id"]
    stroke = {"points": [{"x": 0, "y": 0}, {"x": 0.5, "y": 0.5}], "color": "#c0392b"}
    response = client.put(
        f"/api/docs/0001-question/ink/{block}", json={"quote": "text", "strokes": [stroke]}
    )
    assert response.status_code == 200
    annotation = client.get("/api/docs/0001-question").json()["annotations"][0]
    assert annotation["kind"] == "ink"
    assert annotation["orphaned"] is False
    assert annotation["strokes"][0]["points"][1] == {"x": 0.5, "y": 0.5}


def test_should_orphan_ink_when_its_block_is_rewritten_away(client, store):
    doc = store.create("0001-question", "# Q\n\ntext\n")
    block = client.get("/api/docs/0001-question").json()["blocks"][1]["id"]
    stroke = {"points": [{"x": 0, "y": 0}, {"x": 0.5, "y": 0.5}]}
    client.put(f"/api/docs/0001-question/ink/{block}", json={"quote": "text", "strokes": [stroke]})
    client.put(
        "/api/docs/0001-question",
        json={"markdown": "# Q\n\nrewritten\n", "rev": doc.rev},
    )
    annotation = client.get("/api/docs/0001-question").json()["annotations"][0]
    assert annotation["orphaned"] is True


def test_should_remove_an_annotation_whose_anchor_an_edit_took_away(client, store):
    doc = store.create("0001-question", "# Q\n\ntext\n")
    _note(client, "0001-question")
    client.put(
        "/api/docs/0001-question",
        json={"markdown": "# Q\n\nrewritten\n", "rev": doc.rev},
    )
    lost = client.get("/api/docs/0001-question").json()["annotations"][0]
    assert lost["orphaned"] is True

    assert client.delete(f"/api/docs/0001-question/annotations/{lost['id']}").status_code == 200
    assert client.get("/api/docs/0001-question").json()["annotations"] == []


def test_should_answer_404_when_removing_an_annotation_that_is_not_there(client, store):
    store.create("0001-question", "# Q\n\ntext\n")
    assert client.delete("/api/docs/0001-question/annotations/a9").status_code == 404


def test_should_say_where_a_quote_lands_and_store_it_in_one_request(client, store):
    # One request, because two would leave a window in which an edit moves the passage the
    # quote was found on; the quote may carry its markdown, and the board renders it.
    store.create(
        "0001-question",
        "# Q\n\n| Name | Value |\n| --- | --- |\n| foo | bar |\n\nSee [zrb](https://x.y).\n",
    )

    def noted(quote):
        return client.post(
            "/api/docs/0001-question/notes",
            json={"quote": quote, "note": "here", "author": "tara"},
        )

    row = noted("| foo | bar |")
    assert row.status_code == 201
    assert row.json()["quote"] == "foo bar"
    assert noted("See [zrb](https://x.y).").json()["quote"] == "See zrb ."
    assert noted("foo bar").json()["block"]
    stored = client.get("/api/docs/0001-question").json()["annotations"]
    assert len(stored) == 3 and all(not one["orphaned"] for one in stored)
    # Nothing holds it: 422 and a sentence saying so.
    missed = noted("not there")
    assert missed.status_code == 422 and "No block holds" in missed.json()["detail"]
    assert client.post("/api/docs/nope/notes", json={"quote": "x"}).status_code == 404


def test_should_refuse_a_note_whose_document_moved_while_it_was_placed(
    client, store, monkeypatch
):
    # Finding the block is a render, not a write, so a save can land between the read and
    # the store. The note states the revision it was placed against; when the file is not
    # that revision any more -- an editor saving it, outside this process -- the note is
    # refused and the caller may look again, rather than being stored against a block the
    # page no longer has.
    from board import app as app_module

    doc = store.create("0001-question", "# Q\n\nalpha\n")
    real_locate = app_module.quoting.locate

    def locating_through_an_edit(markdown, quote):
        found = real_locate(markdown, quote)
        store.write("0001-question", "# Q\n\nbeta\n", doc.rev)
        return found

    monkeypatch.setattr(app_module.quoting, "locate", locating_through_an_edit)
    refused = client.post(
        "/api/docs/0001-question/notes", json={"quote": "alpha", "note": "x"}
    )
    assert refused.status_code == 409
    assert "changed" in refused.json()["detail"]
    assert store.annotations("0001-question") == []


def test_should_create_a_document_through_the_board(client):
    # The route `store.create` was written for: before it, a document could only be
    # added by writing the file into docs/ by hand.
    created = client.post(
        "/api/docs", json={"name": "0013-new", "markdown": "# New\n\ntext\n"}
    )
    assert created.status_code == 201
    assert created.json()["rev"]
    assert client.get("/api/docs/0013-new").json()["title"] == "New"


def test_should_refuse_a_second_create_and_leave_the_first_document_alone(client):
    # The failure mode a create route has to rule out is create-then-overwrite: a name
    # that is taken is a refusal, never a fresh start over somebody's page.
    client.post("/api/docs", json={"name": "0013-new", "markdown": "# New\n\nfirst\n"})
    again = client.post(
        "/api/docs", json={"name": "0013-new", "markdown": "# New\n\nsecond\n"}
    )
    assert again.status_code == 409
    assert "already here" in again.json()["detail"]
    assert client.get("/api/docs/0013-new").json()["markdown"] == "# New\n\nfirst\n"


def test_should_refuse_a_name_that_could_leave_the_docs_directory(client, root):
    refused = client.post("/api/docs", json={"name": "../outside", "markdown": "# N\n"})
    assert refused.status_code == 422
    assert not (root.parent / "outside.md").exists()
    assert list((root / "docs").iterdir()) == []


def test_should_remove_a_document_once_its_notes_are_closed(client, store):
    store.create("0001-question", "# Q\n\ntext\n")
    note = _note(client, "0001-question").json()

    refused = client.delete("/api/docs/0001-question")
    assert refused.status_code == 409
    assert "open note" in refused.json()["detail"]
    assert client.get("/api/docs/0001-question").status_code == 200

    client.post(f"/api/docs/0001-question/annotations/{note['id']}/resolve")
    assert client.delete("/api/docs/0001-question").status_code == 200
    assert client.get("/api/docs/0001-question").status_code == 404
    # The notes go with the document: a meta file left behind is the orphan in .board/.
    assert not (store.meta_dir / "0001-question.annotations.json").exists()
    assert client.get("/api/docs").json()["docs"] == []


def test_should_answer_404_when_removing_a_document_that_is_not_there(client):
    assert client.delete("/api/docs/missing").status_code == 404


def test_should_retire_every_mark_when_no_stamp_is_named(client):
    for text in ["board: 0001-one", "board: 0002-two"]:
        client.post("/api/inbox", json={"text": text})
    assert client.post("/api/inbox/retire").json() == {"retired": 2}
    assert client.get("/api/inbox").text == ""
    assert client.post("/api/inbox/retire").json() == {"retired": 0}


def test_should_refuse_a_stamp_it_cannot_read_rather_than_empty_the_queue(client):
    client.post("/api/inbox", json={"text": "board: 0001-one"})
    refused = client.post("/api/inbox/retire", json={"upto": "yesterday"})
    assert refused.status_code == 422
    assert "board: 0001-one" in client.get("/api/inbox").text


def test_should_keep_a_marker_to_one_line_when_its_text_has_breaks(client):
    # A mark that carried a line break would be retired as two marks, one of which the
    # caller never made. The words survive; the break does not.
    client.post(
        "/api/inbox",
        json={"text": "board: 0001-one\n- 2099-01-01T00:00:00+00:00 — not a mark"},
    )
    lines = client.get("/api/inbox").text.strip().splitlines()
    assert len(lines) == 1
    assert "not a mark" in lines[0]
