# zrb-board

A turn-based board for a human and a coding agent: markdown files that render
in a browser, with notes pinned to the paragraph they disagree with.

The design — why markdown is the truth, what a block is, why ink is normalised
to its block and why the nudge is a trigger — is in
[`docs/architecture.md`](docs/architecture.md).

The agent does not watch the page. It reads `/api/docs/<name>/state.md`, a
plain-text view of the document with every note placed inline, and writes back
with a revision-checked `PUT`. Turns are the clock; the browser is a window.

## Run it

```bash
cd ~/zrb-board
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # once
.venv/bin/zrb-board                                          # http://127.0.0.1:8765
```

`BOARD_ROOT` points the server at another directory; `BOARD_PORT` changes the
port. It binds `127.0.0.1` only and has no authentication — it is a desk, not a
service.

## The board

```
docs/0001-what-does-the-board-do-that-chat-doesnt.md   the markdown is the truth
.board/0001-....annotations.json                       notes, anchored to quotes
```

- One file per question. Create one by writing the file; the index picks it up.
- Each contribution is prefixed `**Go:**` or `**Tara:**` and nobody edits the
  other's paragraph, only answers below it — so a softened sentence shows in
  `git diff` and a third party can still read the argument.
- Closed questions move to `closed/` rather than being deleted, so the shelf
  keeps its dates.

## Anchors, not coordinates

Every annotation stores the block id, the exact quote, and offsets inside the
block. On each render it is resolved in three steps: the named block still
holds the quote → the quote is found in another block → nothing matches and the
note appears under "the text moved — these lost their anchor". It is never
placed somewhere that merely looks close.

The block id is a hash of the block's visible text, so editing a paragraph
changes its id; that is what the quote fallback is for.

Strokes are stored the same way: normalised coordinates inside their block
(`0.0`–`1.0`, `x` and `y`), never page pixels. A circle drawn around a sentence
follows that sentence through an edit above it, a resize, or a font change.
The stored form is ours; Excalidraw's scene format is absolute-coordinate, so
an `.excalidraw` export is a projection made at export time, not the truth.

## Drawing and editing

`draw` turns the page into a surface: drag to ink, `erase` removes one stroke
at a time — a click within 10px of a stroke, not a click on the ink itself.
Ink is kept per author, so a drawing of yours and one of mine can sit on the
same paragraph without either replacing the other. Erasing the last stroke
deletes that author's note on the block rather than leaving a hollow one.

`edit markdown` puts the raw file in a textarea; `save` (or Cmd/Ctrl-S) writes
it back with the revision you read.

## Diagrams

A ` ```mermaid ` fence is drawn in place by a vendored `mermaid.min.js` — no
network at render time. A diagram that fails to parse stays a code block with
the error on hover, because a blank is worse than its source.

The pen and the diagram are different tools on purpose: ink is geometry pinned
to a block and nobody can edit it, while a diagram is text that both authors
can change. Reach for the pen when words run out; reach for the fence when the
shape itself is the argument.

## API

| Route | Purpose |
|---|---|
| `GET /api/docs` | the board index |
| `GET /api/docs/{name}` | html, blocks, and annotations resolved to blocks |
| `GET /api/docs/{name}/state.md` | **the text view the agent reads** |
| `PUT /api/docs/{name}` | `{markdown, rev}` — 409 with the current text on a stale rev |
| `POST /api/docs/{name}/annotations` | `{block, quote, note, author, start, end}` |
| `POST /api/docs/{name}/annotations/{id}/resolve` | close a note |
| `PUT /api/docs/{name}/ink/{block}` | `{quote, strokes}` — replace the strokes drawn on one block |
| `GET`/`POST /api/inbox` | the "Mark for next message" marker |

## The button is a note, not a bell

"Mark for next message" appends a line to `.board/inbox.md`. It cannot wake the agent —
the agent runs when it is spoken to. The marker waits for the next turn, and
the convention is that the word `board` in a message means: read the inbox,
then read the state of the open documents.

Making the button actually notify would be a change in zrb — a live-context
line carrying the unread count — and is deliberately not built here yet.

## Tests

```bash
.venv/bin/python -m pytest -q      # storage, anchors, digest, routes
npm test                           # pen coordinate maths (node --test)
```
