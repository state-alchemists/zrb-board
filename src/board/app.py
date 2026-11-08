"""HTTP surface: read the board, write a document, leave an annotation."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from board import anchors, digest, render
from board.render import Block
from board.store import Doc, DocNotFound, INK_AUTHOR, RevConflict, Store

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8765
LOOPBACK = "127.0.0.1"


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


class InkBody(BaseModel):
    quote: str = ""
    strokes: list[dict] = Field(default_factory=list)
    author: str = INK_AUTHOR


def default_root() -> Path:
    configured = os.environ.get("BOARD_ROOT")
    return Path(configured).expanduser() if configured else Path.home() / "zrb-board"


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
    store = Store(root or default_root())
    store.ensure_layout()
    app = FastAPI(title="zrb-board", version="0.1.0")
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

    @app.get("/api/inbox", response_class=PlainTextResponse)
    def read_inbox() -> str:
        return store.inbox()

    @app.post("/api/inbox", status_code=201)
    def leave_marker(body: InboxBody) -> dict:
        store.notify(body.text)
        return {"ok": True}


def main() -> None:
    port = int(os.environ.get("BOARD_PORT", DEFAULT_PORT))
    uvicorn.run(create_app(), host=LOOPBACK, port=port)


if __name__ == "__main__":
    main()
