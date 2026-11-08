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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REV_LENGTH = 12
TITLE_PREFIX = "# "


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


def _next_id(annotations: list[dict]) -> str:
    highest = 0
    for annotation in annotations:
        digits = annotation.get("id", "").lstrip("a")
        if digits.isdigit():
            highest = max(highest, int(digits))
    return f"a{highest + 1}"


class Store:
    """Documents and their annotations, on one filesystem root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.docs_dir = root / "docs"
        self.meta_dir = root / ".board"
        self.inbox_path = self.meta_dir / "inbox.md"

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
        current = self.read(name)
        if current.rev != rev:
            raise RevConflict(current.rev, current.markdown)
        self._atomic_write(self._doc_path(name), markdown)
        return self.read(name)

    def create(self, name: str, markdown: str) -> Doc:
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

    def add_annotation(self, name: str, payload: dict) -> dict:
        self.read(name)
        annotations = self.annotations(name)
        annotation = {
            "id": _next_id(annotations),
            "block": payload["block"],
            "quote": payload["quote"],
            "start": payload["start"],
            "end": payload["end"],
            "note": payload["note"],
            "author": payload["author"],
            "strokes": payload.get("strokes", []),
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "resolved": False,
        }
        self._write_annotations(name, [*annotations, annotation])
        return annotation

    def set_resolved(self, name: str, annotation_id: str, resolved: bool) -> dict:
        annotations = self.annotations(name)
        for annotation in annotations:
            if annotation["id"] == annotation_id:
                annotation["resolved"] = resolved
                self._write_annotations(name, annotations)
                return annotation
        raise DocNotFound(f"annotation {annotation_id}")

    def inbox(self) -> str:
        if not self.inbox_path.is_file():
            return ""
        return self.inbox_path.read_text(encoding="utf-8")

    def notify(self, text: str) -> None:
        """Leave a marker. It cannot wake the agent; it waits for the next turn."""
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        entry = f"- {stamp} — {text.strip() or 'read the board'}\n"
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
