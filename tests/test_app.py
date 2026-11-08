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
