"""The board's storage: markdown under docs/, annotations under .board/.

The markdown is the truth and carries its own revision — the hash of its
bytes. Every write states the revision it read, so a stale write is refused
instead of silently overwriting the other person's paragraph.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REV_LENGTH = 12
TITLE_PREFIX = "# "
NOTE_KIND = "note"
INK_KIND = "ink"
INK_AUTHOR = "go"


class BoardError(Exception):
    """Base for everything the board refuses to do."""


class InvalidName(BoardError):
    """A document name that could escape the docs directory."""


class DocNotFound(BoardError):
    """No such document on this board."""


class DocExists(BoardError):
    """A document by that name is already here."""


class RevConflict(BoardError):
    """The caller wrote from a stale revision; here is what is on disk now."""

    def __init__(self, rev: str, markdown: str) -> None:
        super().__init__(f"stale revision, current is {rev}")
        self.rev = rev
        self.markdown = markdown


@dataclass(frozen=True, slots=True)
class Doc:
    name: str
    markdown: str
    rev: str
    updated: datetime

    @property
    def title(self) -> str:
        for line in self.markdown.splitlines():
            if line.startswith(TITLE_PREFIX):
                return line[len(TITLE_PREFIX) :].strip()
        return self.name


def content_rev(markdown: str) -> str:
    return hashlib.sha1(markdown.encode("utf-8")).hexdigest()[:REV_LENGTH]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _next_id(annotations: list[dict]) -> str:
    highest = 0
    for annotation in annotations:
        digits = annotation.get("id", "").lstrip("a")
        if digits.isdigit():
            highest = max(highest, int(digits))
    return f"a{highest + 1}"


class Store:
    """Documents and their annotations, on one filesystem root.

    The folders under the root can be named directly, because they are not
    always `root/docs` and `root/.board`: the board keeps them in its own
    folder, which its configuration may place anywhere.
    """

    def __init__(
        self,
        root: Path,
        docs_dir: Path | None = None,
        meta_dir: Path | None = None,
    ) -> None:
        self.root = root
        self.docs_dir = docs_dir or root / "docs"
        self.meta_dir = meta_dir or root / ".board"
        self.inbox_path = self.meta_dir / "inbox.md"
        # One writer at a time. The routes are plain `def`s, so the server runs them on
        # a threadpool and two requests can interleave a read with a write. Re-entrant,
        # because a caller holds a transaction and the store's own methods take it again.
        # It is per process: a board run with several workers would need a file lock, and
        # this one is run as a single process.
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Hold the board still while you read it, decide, and write back.

        A revision cannot promise this on its own: a note is placed by rendering the
        document, and the render is not a write, so an edit can land between the read and
        the annotation. Inside a transaction nothing else in this process writes, and the
        expected revision catches what writes from outside it -- an editor saving the file.
        """
        with self._lock:
            yield

    def ensure_layout(self) -> None:
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

    def list_docs(self) -> list[Doc]:
        return [self.read(path.stem) for path in sorted(self.docs_dir.glob("*.md"))]

    def read(self, name: str) -> Doc:
        path = self._doc_path(name)
        if not path.is_file():
            raise DocNotFound(name)
        markdown = path.read_text(encoding="utf-8")
        stamp = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        return Doc(name, markdown, content_rev(markdown), stamp)

    def write(self, name: str, markdown: str, rev: str) -> Doc:
        with self._lock:
            current = self.read(name)
            if current.rev != rev:
                raise RevConflict(current.rev, current.markdown)
            self._atomic_write(self._doc_path(name), markdown)
            return self.read(name)

    def create(self, name: str, markdown: str) -> Doc:
        with self._lock:
            path = self._doc_path(name)
            if path.exists():
                raise DocExists(name)
            self._atomic_write(path, markdown)
            return self.read(name)

    def annotations(self, name: str) -> list[dict]:
        path = self._annotation_path(name)
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("annotations", [])

    def add_annotation(
        self, name: str, payload: dict, expected_rev: str | None = None
    ) -> dict:
        """Store a note. With *expected_rev*, refuse if the document has moved since.

        The revision matters here because a note's block was found by reading the
        document: if it changed between that read and this write, the block names a
        passage the document no longer has, and the note would be born orphaned.
        """
        with self._lock:
            doc = self.read(name)
            if expected_rev is not None and doc.rev != expected_rev:
                raise RevConflict(doc.rev, doc.markdown)
            annotations = self.annotations(name)
            annotation = {
                "id": _next_id(annotations),
                "kind": NOTE_KIND,
                "block": payload["block"],
                "quote": payload["quote"],
                "start": payload["start"],
                "end": payload["end"],
                "note": payload["note"],
                "author": payload["author"],
                "strokes": payload.get("strokes", []),
                "created": _now(),
                "resolved": False,
            }
            self._write_annotations(name, [*annotations, annotation])
            return annotation

    def set_ink(
        self,
        name: str,
        block: str,
        quote: str,
        strokes: list[dict],
        author: str = INK_AUTHOR,
    ) -> dict:
        """Replace the ink *this author* drew on one block, leaving the other's alone."""
        with self._lock:
            self.read(name)
            annotations = self.annotations(name)
            existing = next(
                (
                    annotation
                    for annotation in annotations
                    if annotation.get("kind") == INK_KIND
                    and annotation.get("block") == block
                    and annotation.get("author") == author
                ),
                None,
            )
            if existing is not None:
                return self._rewrite_ink(name, annotations, existing, strokes)
            if not strokes:
                return {}
            created = {
                "id": _next_id(annotations),
                "kind": INK_KIND,
                "block": block,
                "quote": quote,
                "start": 0,
                "end": 0,
                "note": "",
                "author": author,
                "strokes": strokes,
                "created": _now(),
                "resolved": False,
            }
            self._write_annotations(name, [*annotations, created])
            return created

    def _rewrite_ink(
        self, name: str, annotations: list[dict], existing: dict, strokes: list[dict]
    ) -> dict:
        if strokes:
            existing["strokes"] = strokes
            self._write_annotations(name, annotations)
            return existing
        remaining = [item for item in annotations if item["id"] != existing["id"]]
        self._write_annotations(name, remaining)
        return {"id": existing["id"], "removed": True}

    def set_resolved(self, name: str, annotation_id: str, resolved: bool) -> dict:
        with self._lock:
            annotations = self.annotations(name)
            for annotation in annotations:
                if annotation["id"] == annotation_id:
                    annotation["resolved"] = resolved
                    self._write_annotations(name, annotations)
                    return annotation
            raise DocNotFound(f"annotation {annotation_id}")

    def remove_annotation(self, name: str, annotation_id: str) -> dict:
        """Drop an annotation for good.

        Resolving keeps the note on the shelf; removal is for a note whose
        paragraph an edit took away, where there is nothing left to resolve
        against and the card would sit in the lost-and-found for ever.
        """
        with self._lock:
            annotations = self.annotations(name)
            remaining = [item for item in annotations if item["id"] != annotation_id]
            if len(remaining) == len(annotations):
                raise DocNotFound(f"annotation {annotation_id}")
            self._write_annotations(name, remaining)
            return {"id": annotation_id, "removed": True}

    def inbox(self) -> str:
        if not self.inbox_path.is_file():
            return ""
        return self.inbox_path.read_text(encoding="utf-8")

    def notify(self, text: str) -> None:
        """Leave a marker. It cannot wake the agent; it waits for the next turn."""
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        entry = f"- {stamp} — {text.strip() or 'read the board'}\n"
        with self._lock:
            existing = self.inbox()
            self._atomic_write(self.inbox_path, existing + entry)

    def _doc_path(self, name: str) -> Path:
        if not NAME_PATTERN.match(name) or name.endswith(".md"):
            raise InvalidName(name)
        return self.docs_dir / f"{name}.md"

    def _annotation_path(self, name: str) -> Path:
        return self.meta_dir / f"{name}.annotations.json"

    def _write_annotations(self, name: str, annotations: list[dict]) -> None:
        payload = json.dumps({"annotations": annotations}, indent=2, ensure_ascii=False)
        self._atomic_write(self._annotation_path(name), payload + "\n")

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        os.replace(temporary, path)
