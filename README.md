# zrb-board

A turn-based board for a human and a coding agent: markdown files that render
in a browser, with notes pinned to the paragraph they disagree with.

The design — why markdown is the truth, what a block is, why ink is normalised
to its block and why the nudge is a trigger — is in
[`docs/architecture.md`](docs/architecture.md).

The agent does not watch the page. It reads `/api/docs/<name>/state.md`, a
plain-text view of the document with every note placed inline, and writes back
with a revision-checked `PUT`. Turns are the clock; the browser is a window.

## Install

```bash
pip install zrb-board
zrb-board          # http://127.0.0.1:8765
```

The wheel carries the page — the CSS, the pen and the vendored Mermaid bundle — so an
installed board draws diagrams with no network at render time.

## Run it from a checkout

```bash
cd ~/zrb-board
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # once
.venv/bin/zrb-board                                          # http://127.0.0.1:8765
```

Your documents are not in this checkout. They live in `~/.zrb/board`, the same
shape as zrb-avatar's `~/.zrb/avatar`:

```
~/.zrb/board/docs/0001-what-…md      the markdown is the truth
~/.zrb/board/.board/0001-….annotations.json    notes, anchored to quotes
~/.zrb/board/.board/inbox.md         what "Mark for next message" appended
```

Every setting is an environment variable, `$ZRB_BOARD_<NAME>` — with the `ZRB`
read from `_ZRB_ENV_PREFIX`, so a white-labeled zrb needs no code change:

| Variable | Default | What it does |
|---|---|---|
| `ZRB_BOARD_HOME` | `~/.zrb/board` | the folder holding `docs/` and `.board/` |
| `ZRB_BOARD_DOCS` | `<home>/docs` | the documents folder alone |
| `ZRB_BOARD_META` | `<home>/.board` | the notes folder alone |
| `ZRB_BOARD_HOST` | `127.0.0.1` | the address to bind |
| `ZRB_BOARD_PORT` | `8765` | the port |
| `ZRB_BOARD_NUDGE` | unset — off | `1` lets an unread mark start a chat turn on its own |
| `ZRB_BOARD_NUDGE_EVERY` | `15` | seconds between the trigger's looks at the inbox |

The last two are read by the chat's trigger, not by the server: setting them changes nothing
about the board that is serving.

It binds loopback only and has no authentication — it is a desk, not a service.

`8765` is quad digits, easy to say and to type, and nothing is registered to it. It only has
to stay clear of zrb's web port, `21213`, which a board URL is often pasted next to. It is a
default rather than a decision: `ZRB_BOARD_PORT` moves it.

## Run it in the background

```bash
zrb-board -d        # starts a detached copy, writes ~/.zrb/board/board.pid, exits
zrb-board status    # running or not, which pid, and whether it answers
zrb-board stop      # SIGTERM that pid, and remove the file
```

`-d` spawns a second copy of the program with no terminal attached and points its output at
`~/.zrb/board/board.log`; nothing is left holding it, so closing the terminal does not close
the board. On POSIX the child gets a session of its own (`start_new_session`); on Windows it
gets no console (`DETACHED_PROCESS`) and a process group of its own. Those are the same
promise by two names, and it is the only platform difference in here.

What it does **not** do, on purpose: restart after a crash, or start when the machine boots.
Both need a supervisor, and every system ships one — `systemd --user` on Linux, Task
Scheduler on Windows, `launchd` on macOS. A unit file is four lines:

```ini
# ~/.config/systemd/user/zrb-board.service
[Unit]
Description=zrb-board
[Service]
ExecStart=%h/.local/bin/zrb-board
Restart=on-failure
[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now zrb-board
```

## The board

```
~/.zrb/board/docs/0001-what-does-the-board-do-that-chat-doesnt.md   the truth
~/.zrb/board/.board/0001-….annotations.json                          the notes beside it
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
| `POST /api/docs` | `{name, markdown}` — 201 with the rev, 409 when the name is taken |
| `PUT /api/docs/{name}` | `{markdown, rev}` — 409 with the current text on a stale rev |
| `DELETE /api/docs/{name}` | the document and its notes together — 409 while a note is open |
| `POST /api/docs/{name}/notes` | `{quote, note, author}` — locate the quote and store the note in one request |
| `POST /api/docs/{name}/annotations` | `{block, quote, note, author, start, end}` |
| `POST /api/docs/{name}/annotations/{id}/resolve` | close a note |
| `PUT /api/docs/{name}/ink/{block}` | `{quote, strokes}` — replace the strokes drawn on one block |
| `GET`/`POST /api/inbox` | the "Mark for next message" marker |
| `POST /api/inbox/retire` | `{upto}` — retire the marks up to a stamp, or every mark |

## The agent's tools

`src/board/tools.py` is the other side of that table: seven tools an LLM agent calls, in
the same tree as the routes they call, so a route that changes here changes its caller in
the same diff. Nothing in the server imports them — `board.app` runs without zrb.

| Tool | What it does |
|---|---|
| `BoardState` | the marks waiting, then every document with its revision and its open notes (ink and notes counted apart) |
| `BoardRead` | one document as text: the view — `rev`, then each note beside the passage it is about — or, with `source=True`, the document's own markdown byte for byte, which is the text `BoardWrite` takes |
| `BoardWrite` | the whole markdown back, stating the revision that was read; a stale revision writes nothing and returns the current text to merge into |
| `BoardCreate` | a new document under a name nothing uses; a name that is taken is a 409, never an overwrite |
| `BoardNote` | a note on a quoted passage — the quote is sent to the board, which renders it where it stands (a table row under its header, a link as its words) and returns the block and the words that block holds |
| `BoardResolveNote` | close a note by the id `BoardRead` shows; ink is refused, because there is nothing to close |
| `BoardRetireMarks` | retire the marks the work is done for, up to a stamp or all of them — reading the inbox is not answering it |

They are registered with zrb by `src/board/zrb_init.py`, which a shell points at:

```sh
export ZRB_INIT_SCRIPTS="$ZRB_INIT_SCRIPTS:$(zrb-board init-path)"
```

`BOARD_URL` overrides the address; without it the tools call the host and port the server
binds (`ZRB_BOARD_HOST`, `ZRB_BOARD_PORT`). `BOARD_AUTHOR` signs a note, `agent` unless it
is set. The tools read the marker folder as a whole and fold it by document, because
nothing ever clears it.

## The button is a note, and a nudge you switch on

"Mark for next message" appends one line to `.board/inbox.md` in the board's home folder —
the line names the document, and carries the passage you had selected. The agent reads it
the next time it is spoken to, and the convention is that the word `board` in a message
means: read the inbox, then read the state of the open documents.

The board can also start that turn itself. `src/board/zrb_init.py` registers a chat trigger
beside the seven tools (`zrb/llm/ui/trigger.py`), so with `ZRB_BOARD_NUDGE=1` an unread mark
becomes a turn with nobody typing: the agent reads the document, answers the passage **in
the margin beside it** with `BoardNote`, and retires the mark it answered. A mark made with
nothing selected has no quotation to anchor a note to, so that answer comes back in the
chat, and the trigger says so rather than inventing a quotation.

Three things the trigger is careful about:

- **off by default** — a program that starts turns on your behalf should be one you switched
  on; `ZRB_BOARD_NUDGE_EVERY` sets how often it looks, 15 seconds otherwise
- **one turn per mark** — a ledger of consumed stamps in `.board/nudge.json`, written before
  the turn starts, so a restart cannot answer the same mark twice even when the turn that
  did answer it died before it retired the line
- **the server need not be up** — the inbox is read as a file, so a mark left while the board
  is stopped still becomes a turn

### What this section used to say

It said the button could only ever be "a live-context line carrying the unread count", and
that making it notify was deliberately not built. That describes the mechanism
`docs/architecture.md` §7 rejects: a line in the prompt is passive, so the mark still waits
for someone to speak. The description was wrong before the code was.

## Tests

```bash
.venv/bin/python -m pytest -q      # storage, anchors, digest, routes
npm test                           # pen coordinate maths (node --test)
```

Both suites run in CI on four Pythons and on macOS. A pull request opened from a branch of
this repository also gets a review posted on it — `zrb` reads the diff with the model key,
from a workflow that `pull_request_target` resolves out of `main`, so a branch cannot edit
the reviewer that looks at it. A pull request from a fork is not reviewed: the workflow's
guard asks for a head repository it cannot be. `test_ci.py` is what keeps those workflows
pinned to commits and to named runner images.
