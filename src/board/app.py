"""HTTP surface: read the board, write a document, leave an annotation."""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from board import anchors, config, digest, quoting, render
from board.render import Block
from board.store import Doc, DocNotFound, INK_AUTHOR, RevConflict, Store

STATIC_DIR = Path(__file__).parent / "static"


def installed_version() -> str:
    """The version in the metadata, so the OpenAPI document cannot drift from pyproject.

    The metadata is the version that is *installed*, which is the one running: a
    `pip install zrb-board` always matches its wheel. An editable checkout keeps the
    version it was installed as until it is reinstalled, and one that was never installed
    has none at all -- the app is still servable, so it says 0.0.0 rather than refusing to
    start.
    """
    try:
        return package_version("zrb-board")
    except PackageNotFoundError:
        return "0.0.0"


class WriteBody(BaseModel):
    markdown: str
    rev: str


class AnnotationBody(BaseModel):
    block: str
    quote: str
    note: str
    author: str = "go"
    start: int = 0
    end: int = 0


class InboxBody(BaseModel):
    text: str = ""


class NoteBody(BaseModel):
    quote: str = ""
    note: str = ""
    author: str = "agent"


class InkBody(BaseModel):
    quote: str = ""
    strokes: list[dict] = Field(default_factory=list)
    author: str = INK_AUTHOR


def default_root() -> Path:
    """Where the board lives: `$ZRB_BOARD_HOME`, `~/.zrb/board` by default."""
    return config.home()


def _resolved(annotation: dict, blocks: list[Block]) -> dict:
    """Say where the annotation lands now, or that it lost its anchor."""
    match = anchors.resolve(annotation, blocks)
    block_id, offset = match if match else (None, None)
    return {
        **annotation,
        "resolved_block": block_id,
        "resolved_offset": offset,
        "orphaned": match is None,
    }


def _doc_summary(store: Store, doc: Doc) -> dict:
    """The index view: no markup, no blocks — just enough to list the board."""
    return {
        "name": doc.name,
        "title": doc.title,
        "rev": doc.rev,
        "updated": doc.updated.isoformat(timespec="seconds"),
        "annotations": store.annotations(doc.name),
    }


def _doc_payload(store: Store, doc: Doc) -> dict:
    rendered = render.render(doc.markdown)
    annotations = [
        _resolved(annotation, rendered.blocks)
        for annotation in store.annotations(doc.name)
    ]
    return {
        "name": doc.name,
        "title": doc.title,
        "rev": doc.rev,
        "updated": doc.updated.isoformat(timespec="seconds"),
        "markdown": doc.markdown,
        "html": rendered.html,
        "blocks": [{"id": block.id, "text": block.text} for block in rendered.blocks],
        "annotations": annotations,
    }


def _not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no document named {name}")


def create_app(root: Path | None = None) -> FastAPI:
    if root is None:
        store = Store(
            config.home(), docs_dir=config.docs_dir(), meta_dir=config.meta_dir()
        )
    else:
        store = Store(root)
    store.ensure_layout()
    app = FastAPI(title="zrb-board", version=installed_version())
    _register_routes(app, store)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _register_routes(app: FastAPI, store: Store) -> None:
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/d/{name}", include_in_schema=False)
    def document_page(name: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "doc.html")

    @app.get("/api/docs")
    def list_documents() -> dict:
        return {"docs": [_doc_summary(store, doc) for doc in store.list_docs()]}

    @app.get("/api/docs/{name}")
    def read_document(name: str) -> dict:
        try:
            return _doc_payload(store, store.read(name))
        except DocNotFound:
            raise _not_found(name) from None

    @app.put("/api/docs/{name}")
    def write_document(name: str, body: WriteBody) -> JSONResponse:
        try:
            doc = store.write(name, body.markdown, body.rev)
        except DocNotFound:
            raise _not_found(name) from None
        except RevConflict as conflict:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "stale revision — re-read before writing",
                    "rev": conflict.rev,
                    "markdown": conflict.markdown,
                },
            )
        return JSONResponse(content={"rev": doc.rev, "updated": doc.updated.isoformat()})

    @app.post("/api/docs/{name}/annotations", status_code=201)
    def add_annotation(name: str, body: AnnotationBody) -> dict:
        try:
            return store.add_annotation(name, body.model_dump())
        except DocNotFound:
            raise _not_found(name) from None

    @app.post("/api/docs/{name}/annotations/{annotation_id}/resolve")
    def resolve_annotation(name: str, annotation_id: str) -> dict:
        try:
            return store.set_resolved(name, annotation_id, resolved=True)
        except DocNotFound:
            raise _not_found(annotation_id) from None

    @app.delete("/api/docs/{name}/annotations/{annotation_id}")
    def remove_annotation(name: str, annotation_id: str) -> dict:
        try:
            return store.remove_annotation(name, annotation_id)
        except DocNotFound:
            raise _not_found(annotation_id) from None

    @app.put("/api/docs/{name}/ink/{block}")
    def draw_on_block(name: str, block: str, body: InkBody) -> dict:
        try:
            return store.set_ink(name, block, body.quote, body.strokes, body.author)
        except DocNotFound:
            raise _not_found(name) from None

    @app.get("/api/docs/{name}/state.md", response_class=PlainTextResponse)
    def read_state(name: str) -> str:
        try:
            doc = store.read(name)
        except DocNotFound:
            raise _not_found(name) from None
        return digest.build_digest(doc, store.annotations(name))

    @app.post("/api/docs/{name}/notes", status_code=201)
    def add_note(name: str, body: NoteBody) -> dict:
        """A note from a quote: located and stored in the same request.

        The quote may arrive with its markdown -- it is read from the source by an agent,
        or seen on the page -- and it is located against the document as this request reads
        it. Locating and storing are one call on purpose: two would leave a window in
        which an edit moves the passage the quote was found on, and the note would land on
        the wrong words or on none.
        """
        try:
            doc = store.read(name)
        except DocNotFound:
            raise _not_found(name) from None
        found = quoting.locate(doc.markdown, body.quote)
        if isinstance(found, str):
            # 422: the quote was read fine, it is the passage that cannot be anchored.
            raise HTTPException(status_code=422, detail=found)
        block, words = found
        return store.add_annotation(
            name,
            {
                "block": block,
                "quote": words,
                "note": body.note,
                "author": body.author,
                "start": 0,
                "end": 0,
            },
        )

    @app.get("/api/inbox", response_class=PlainTextResponse)
    def read_inbox() -> str:
        return store.inbox()

    @app.post("/api/inbox", status_code=201)
    def leave_marker(body: InboxBody) -> dict:
        store.notify(body.text)
        return {"ok": True}


def main() -> None:
    # `zrb-board init-path` is how a shell points ZRB_INIT_SCRIPTS at the agent's side of
    # the board (board/zrb_init.py); it prints a path and starts nothing.
    if sys.argv[1:] == ["init-path"]:
        print(Path(__file__).with_name("zrb_init.py"))
        return
    uvicorn.run(create_app(), host=config.host(), port=config.port())


if __name__ == "__main__":
    main()
