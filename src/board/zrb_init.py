"""The board's zrb side: load it and the agent gets seven tools for the board, and a nudge.

    export ZRB_INIT_SCRIPTS="$ZRB_INIT_SCRIPTS:$(zrb-board init-path)"

The tools themselves are `board/tools.py` and the trigger is `board/nudge.py`; this file
only puts them on zrb's chat task. The trigger yields nothing unless `ZRB_BOARD_NUDGE` is
set, so loading this file does not make the agent start turns behind anybody's back.
Nothing in the server imports it, and `board.app` runs without zrb installed.

Read on the first turn the user says "board" (the marker's own convention): `BoardState`
prints what is waiting and what documents there are, `BoardRead` prints one as text with
its notes in the margin, and the rest write. The names are PascalCase so a transcript
reads them as tools rather than as Python.

`sys.path` is put in front of the interpreter's own, not after: in a checkout,
`src/board/zrb_init.py` is loaded without the package being installed, and the tools must
come from the tree this file was loaded from. When it is installed, that is site-packages,
which is already where the import would have gone.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zrb.builtin import llm_chat  # noqa: E402

from board.nudge import board_nudge  # noqa: E402
from board.tools import (  # noqa: E402
    BoardCreate,
    BoardNote,
    BoardRead,
    BoardResolveNote,
    BoardRetireMarks,
    BoardState,
    BoardWrite,
)

for tool in (
    BoardState,
    BoardRead,
    BoardWrite,
    BoardCreate,
    BoardNote,
    BoardResolveNote,
    BoardRetireMarks,
):
    llm_chat.append_tool(tool)

# The nudge: a mark left in the browser can start a turn instead of waiting for one. It is
# registered unconditionally and returns immediately unless `ZRB_BOARD_NUDGE` is set, so the
# switch is a board setting rather than a second script to install. Read it, and the rest of
# the reasoning, in `board/nudge.py`.
llm_chat.append_trigger(board_nudge)
