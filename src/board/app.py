"""HTTP surface: read the board, write a document, leave an annotation."""

from __future__ import annotations

import sys
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from board import anchors, config, daemon, digest, quoting, render
from board.render import Block
from board.store import (
    Doc,
    DocExists,
    DocNotFound,
    HasOpenNotes,
    INK_AUTHOR,
    InvalidName,
    RevConflict,
    Store,
    UnknownStamp,
)

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


class CreateBody(BaseModel):
    name: str
    markdown: str


class AnnotationBody(BaseModel):
    block: str
    quote: str
    note: str
    author: str = "go"
    start: int = 0
    end: int = 0


class InboxBody(BaseModel):
    text: str = ""


class RetireBody(BaseModel):
    upto: str = ""


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

    @app.post("/api/docs", status_code=201)
    def create_document(body: CreateBody) -> JSONResponse:
        """Add a document. A name already in use is a 409, never an overwrite.

        `PUT` answers 404 for a name that is not here, so before this route the only
        way to add a document was to write the file into `docs/` by hand -- which
        skipped the name check, the exists check and the atomic write that
        `store.create` is made of, for a caller the board is supposed to serve.
        """
        try:
            doc = store.create(body.name, body.markdown)
        except InvalidName:
            raise HTTPException(
                status_code=422,
                detail=f"{body.name!r} is not a document name: letters, digits, dots, "
                "dashes and underscores, starting with a letter or a digit, and never "
                "a path out of docs/",
            ) from None
        except DocExists:
            raise HTTPException(
                status_code=409,
                detail=f"a document named {body.name} is already here: read it, then "
                "write with its revision rather than starting again over it",
            ) from None
        return JSONResponse(
            status_code=201,
            content={"rev": doc.rev, "updated": doc.updated.isoformat(timespec="seconds")},
        )

    @app.delete("/api/docs/{name}")
    def delete_document(name: str) -> dict:
        """Remove a document and its annotations together. 409 while a note is open."""
        try:
            return store.remove(name)
        except (DocNotFound, InvalidName):
            raise _not_found(name) from None
        except HasOpenNotes as refusal:
            raise HTTPException(
                status_code=409,
                detail=f"{name} still holds {refusal}: resolve or remove them first, or "
                "the removal would take the questions with it",
            ) from None

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
        """A note from a quote: located and stored with nothing allowed in between.

        The quote may arrive with its markdown -- it is read from the source by an agent,
        or seen on the page -- and it is located against the document as this request reads
        it. The whole thing is one transaction: the render that finds the block is not a
        write, so without the store held still, an edit could land between finding the
        passage and storing the note, and the note would name a block the page no longer
        has.
        """
        with store.transaction():
            try:
                doc = store.read(name)
            except DocNotFound:
                raise _not_found(name) from None
            found = quoting.locate(doc.markdown, body.quote)
            if isinstance(found, str):
                # 422: the quote was read fine, it is the passage that cannot be anchored.
                raise HTTPException(status_code=422, detail=found)
            block, words = found
            try:
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
                    expected_rev=doc.rev,
                )
            except RevConflict as conflict:
                # Written from outside this process -- an editor saving the file. The
                # quote is worth another read, so the caller may simply ask again.
                raise HTTPException(
                    status_code=409,
                    detail="the document changed while the note was being placed; "
                    "read it again and retry",
                ) from conflict

    @app.get("/api/inbox", response_class=PlainTextResponse)
    def read_inbox() -> str:
        return store.inbox()

    @app.post("/api/inbox", status_code=201)
    def leave_marker(body: InboxBody) -> dict:
        store.notify(body.text)
        return {"ok": True}

    @app.post("/api/inbox/retire")
    def retire_markers(body: RetireBody = RetireBody()) -> dict:
        """Retire the marks that have been dealt with, up to a stamp or all of them.

        A mark is a queue entry nothing dequeued, so "×8" on a document meant eight
        marks ever made. Retiring is its own request because reading the inbox is not
        answering it: a mark cleared before its work is done is a reminder thrown away.
        """
        try:
            return {"retired": store.retire_marks(body.upto)}
        except UnknownStamp as refusal:
            raise HTTPException(
                status_code=422,
                detail=f"{refusal} is not a timestamp the board writes: BoardState "
                "prints the one each mark carries, and a stamp it cannot read would "
                "otherwise retire the whole queue",
            ) from None


USAGE = """\
zrb-board — the board's server, and the agent's side of it

    zrb-board              serve on board.config's host and port
    zrb-board -d           serve in the background, with a pid file and a log
    zrb-board stop         stop the background server
    zrb-board status       whether it is running, and whether it answers
    zrb-board init-path    print board/zrb_init.py, for ZRB_INIT_SCRIPTS
    zrb-board --version    print the installed version
"""


def _board_url() -> str:
    return f"{config.url()}/api/docs"


def background() -> None:
    """Start a detached copy of this program and return; the copy is what serves.

    The pid is written before the wait, so `stop` finds it even if the copy dies on the way
    up. The wait is only so that the command can say whether the board actually answered
    rather than leaving you to guess.

    The answer is re-read inside the lock, not before it: two commands that ask "is it up"
    at the same moment both hear "no", and both would start a server. The lock is what
    turns that pair of reads into one answer.
    """
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    url = _board_url()
    with daemon.only_one(daemon.lock_path(home)) as alone:
        if not alone:
            # Someone else is starting it. Their child is the one that will serve, so wait
            # for it rather than starting a second on the same port.
            for _ in range(50):
                if daemon.answering(url):
                    print(f"Another `zrb-board -d` started it: serving at {url}.")
                    return
                time.sleep(0.1)
            print("Another `zrb-board -d` is starting the board: nothing started.")
            return
        if daemon.answering(url):
            print(f"Already serving at {url}: nothing started.")
            return
        recorded = daemon.read_pid(daemon.pid_path(home))
        if recorded is not None and daemon.running(recorded):
            recorded_id = daemon.read_identity(daemon.pid_path(home))
            if recorded_id and recorded_id == daemon.started_at(recorded):
                # A live pid that does not answer yet is a board still coming up -- slow
                # disk, slow import -- and starting a second one would fight it for the port.
                print(
                    f"pid {recorded} is recorded as the board and is still running, though "
                    f"nothing answers at {url} yet: `zrb-board status` will say more."
                )
                return
            # Something else holds that pid now, so the file is stale rather than slow.
            daemon.pid_path(home).unlink(missing_ok=True)
        log = daemon.log_path(home)
        pid = daemon.detach([sys.executable, "-m", "board.app"], log)
        daemon.write_pid(daemon.pid_path(home), pid)
        for _ in range(50):
            if daemon.answering(url):
                print(f"Serving in the background as pid {pid}: {url}")
                print(f"Log: {log}")
                return
            if not daemon.running(pid):
                break
            time.sleep(0.1)
    print(f"Started pid {pid}, but nothing answers at {url} yet. Log: {log}")


def main() -> None:
    args = sys.argv[1:]
    # `zrb-board init-path` is how a shell points ZRB_INIT_SCRIPTS at the agent's side of
    # the board (board/zrb_init.py); it prints a path and starts nothing.
    if args == ["init-path"]:
        print(Path(__file__).with_name("zrb_init.py"))
        return
    if args in (["-V"], ["--version"]):
        print(installed_version())
        return
    if args in (["-h"], ["--help"]):
        print(USAGE)
        return
    if args in (["-d"], ["--background"]):
        background()
        return
    if args == ["stop"]:
        home = config.home()
        with daemon.only_one(daemon.lock_path(home)) as alone:
            if not alone:
                print("A board is being started or stopped right now: try again in a moment.")
                return
            print(daemon.stop(daemon.pid_path(home)))
        return
    if args == ["status"]:
        print(daemon.describe(daemon.pid_path(config.home()), _board_url()))
        return
    # An argument this program does not know used to start a server anyway: `--version`
    # served the board and said nothing about the version. Say so and start nothing.
    if args:
        print(f"zrb-board: unknown argument {args[0]!r}", file=sys.stderr)
        print("Run `zrb-board --help` for what it takes.", file=sys.stderr)
        raise SystemExit(2)
    uvicorn.run(create_app(), host=config.host(), port=config.port())


if __name__ == "__main__":
    main()
