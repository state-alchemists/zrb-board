"""The board's zrb side: load it and the agent gets five tools for the board.

    export ZRB_INIT_SCRIPTS="$ZRB_INIT_SCRIPTS:$(zrb-board init-path)"

The tools themselves are `board/tools.py`; this file only puts them on zrb's chat task.
Nothing in the server imports it, and `board.app` runs without zrb installed.

Read on the first turn the user says "board" (the marker's own convention): `BoardState`
prints what is waiting and what documents there are, `BoardRead` prints one as text with
its notes in the margin, and the three others write. The names are PascalCase so a
transcript reads them as tools rather than as Python.

`sys.path` is put in front of the interpreter's own, not after: in a checkout,
`src/board/zrb_init.py` is loaded without the package being installed, and the tools must
come from the tree this file was loaded from. When it is installed, that is site-packages,
which is already where the import would have gone.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zrb.builtin import llm_chat  # noqa: E402

from board.tools import (  # noqa: E402
    BoardNote,
    BoardRead,
    BoardResolveNote,
    BoardState,
    BoardWrite,
)

for tool in (BoardState, BoardRead, BoardWrite, BoardNote, BoardResolveNote):
    llm_chat.append_tool(tool)
