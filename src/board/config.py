"""Where the board keeps its documents, and how to point it elsewhere.

Nothing of yours lives in the checkout. A board is a folder of markdown with a
folder of notes beside it:

    <home>/docs       one markdown file per document — the truth
    <home>/.board     annotations, ink, and the marker inbox — the sidecar

<home> is `$ZRB_BOARD_HOME`, `~/.zrb/board` by default: the same shape as
zrb-avatar's `~/.zrb/avatar`. The `ZRB` is not hard-coded. It is read from the
bare `_ZRB_ENV_PREFIX` that zrb takes its own settings prefix from, so a
white-labeled zrb points this at its own variables without a code change here.

Every setting is `$<prefix>_BOARD_<NAME>`:

    HOME    the folder above; `~` is expanded
    DOCS    the documents folder alone, instead of `<home>/docs`
    META    the notes folder alone, instead of `<home>/.board`
    HOST    the address to bind — 127.0.0.1 by default, because this is a desk
    PORT    8765 by default
    NUDGE   `1` lets a mark start a turn on its own; off by default
    NUDGE_EVERY
            seconds between looks at the inbox, 15 by default
    NUDGE_SETTLE
            seconds of no new marks before a turn starts, 30 by default

The environment is read when the server starts, not when this module is
imported, so a test can point `DOCS` at a temporary folder and a shell can
change its mind without anything else being reloaded.
"""

from __future__ import annotations

import os
from pathlib import Path

PREFIX_VAR = "_ZRB_ENV_PREFIX"
DEFAULT_PREFIX = "ZRB"
DEFAULT_HOME = "~/.zrb/board"
DEFAULT_HOST = "127.0.0.1"
# Quad digits, easy to say and to type; nothing is registered to it. It only has to stay
# clear of zrb's web port (21213), which a board URL is often pasted next to -- and it is
# a default, not a decision: ZRB_BOARD_PORT moves it.
DEFAULT_PORT = 8765
# Fifteen seconds: fast enough that a mark feels answered, slow enough that a mark made
# while a turn is running is not read again and again for the length of that turn.
DEFAULT_NUDGE_EVERY = 15.0
# Thirty seconds of no new marks: long enough for a run of three marks made one after
# another to finish, short enough that the answer still feels like a reply to what you did.
DEFAULT_NUDGE_SETTLE = 30.0


def env_name(setting: str, prefix: str | None = None) -> str:
    """The variable that holds a board setting: `HOME` -> `ZRB_BOARD_HOME`."""
    return f"{prefix or os.getenv(PREFIX_VAR) or DEFAULT_PREFIX}_BOARD_{setting}"


def env_get(setting: str, default: str = "", prefix: str | None = None) -> str:
    """A board setting's value, spelled with the prefix zrb is running under."""
    return os.getenv(env_name(setting, prefix), default)


def _folder(setting: str, fallback: Path) -> Path:
    wanted = env_get(setting).strip()
    return Path(os.path.expanduser(wanted)) if wanted else fallback


def home() -> Path:
    """The board's own folder, holding `docs/` and `.board/`."""
    return Path(os.path.expanduser(env_get("HOME", DEFAULT_HOME)))


def docs_dir() -> Path:
    """Where the markdown is read and written."""
    return _folder("DOCS", home() / "docs")


def meta_dir() -> Path:
    """Where the annotations and the marker inbox are kept."""
    return _folder("META", home() / ".board")


def host() -> str:
    return env_get("HOST", DEFAULT_HOST)


def port() -> int:
    return int(env_get("PORT", str(DEFAULT_PORT)))


def nudge_enabled() -> bool:
    """Whether a mark may start a turn. Off unless `..._BOARD_NUDGE` says otherwise.

    A program that starts turns on your behalf should be one you switched on, so the
    absence of the variable is a no, not a default to be guessed at.
    """
    return env_get("NUDGE").strip().lower() in ("1", "true", "yes", "on")


def nudge_every() -> float:
    """How long to wait between looks at the inbox, in seconds."""
    said = env_get("NUDGE_EVERY").strip()
    try:
        return float(said) if said else DEFAULT_NUDGE_EVERY
    except ValueError:
        return DEFAULT_NUDGE_EVERY


def nudge_settle() -> float:
    """How long marking has to have stopped before a turn starts, in seconds.

    A mark is a selection somebody made a second ago and may be about to replace, or the
    first of three. Answering it while the next one is still being dragged is an
    interruption, not help, so the queue waits until nothing new has arrived for this long.
    `0` means "answer the moment a mark lands", which is only right if marks come one at a
    time and slowly.
    """
    said = env_get("NUDGE_SETTLE").strip()
    try:
        return float(said) if said else DEFAULT_NUDGE_SETTLE
    except ValueError:
        return DEFAULT_NUDGE_SETTLE


def url() -> str:
    """Where the board is *reached*, from the address it *binds*.

    A bind address is not always a place to connect: `0.0.0.0` and `::` mean "every
    address", so they are reached at the loopback. An IPv6 literal is bracketed, or the
    colon in it reads as the port separator. Both the agent's tools and the command line
    ask this, so `zrb-board status` and `BoardState` cannot disagree about where it is.
    """
    address = host().strip() or DEFAULT_HOST
    if address in ("0.0.0.0", "::"):
        address = "127.0.0.1"
    elif ":" in address and not address.startswith("["):
        address = f"[{address}]"
    return f"http://{address}:{port()}"
