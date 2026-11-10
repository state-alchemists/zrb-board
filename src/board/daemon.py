"""Run the board in the background, on whichever system this is.

The trick is not a daemon mode in the usual sense: the command starts a second copy of
itself with no terminal attached, points that copy's output at a log file, writes down its
pid, and exits. Nothing is left holding the child -- no shell to hang up on it, no console
to be closed under it -- so it keeps serving until something kills it.

That detachment is the one thing this module does. It deliberately does not restart the
process after a crash, and does not start it at boot: both need a supervisor, and every
system already has one (`systemd --user` on Linux, Task Scheduler on Windows, `launchd` on
macOS). A program that tries to be its own supervisor gets a pid file right and log
rotation wrong.
"""

from __future__ import annotations

import os
import signal
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

PID_NAME = "board.pid"
LOG_NAME = "board.log"


def pid_path(home: Path) -> Path:
    return home / PID_NAME


def log_path(home: Path) -> Path:
    return home / LOG_NAME


def detach(command: list[str], log: Path) -> int:
    """Start *command* with no terminal of its own, and return its pid.

    On POSIX the child is put in a session of its own (`start_new_session`), which is what
    keeps it alive when the shell that ran this exits. That argument only does something on
    POSIX; Windows gets no console (`DETACHED_PROCESS`) and a process group of its own
    instead, which is the same guarantee by another name.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as out:
        shared = {
            "stdin": subprocess.DEVNULL,
            "stdout": out,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "nt":
            process = subprocess.Popen(
                command,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
                **shared,
            )
        else:
            process = subprocess.Popen(command, start_new_session=True, **shared)
    return process.pid


def running(pid: int) -> bool:
    """Whether *pid* names a live process.

    Signal 0 does not deliver anything; it only asks the system whether it could. On POSIX
    that is enough, and a permission error still means the process is there -- but an
    exited process whose parent has not reaped it answers yes until it is collected, so
    Linux's `/proc` is asked the second question.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return _not_a_zombie(pid)


def _not_a_zombie(pid: int) -> bool:
    """Whether a process signal 0 answered for is still doing something.

    A child that has exited and not been reaped is shown as `Z` (defunct). Where there is
    no `/proc` to ask -- macOS, and Windows never gets here -- the answer is yes, because a
    zombie needs a parent that never waits, which a long-lived server does not have.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    fields = stat.rsplit(")", 1)[-1].split()
    return not fields or fields[0] != "Z"


def read_pid(path: Path) -> int | None:
    """The pid in *path*, or None when it is missing or is not a number."""
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def answering(url: str, timeout: float = 1.0) -> bool:
    """Whether something at *url* answers at all -- the cheapest "is the board up"."""
    try:
        urllib.request.urlopen(url, timeout=timeout).close()
    except (urllib.error.URLError, OSError):
        return False
    return True


def stop(path: Path) -> str:
    """Stop the process named in *path*, and say what happened.

    SIGTERM, which uvicorn turns into a clean shutdown on POSIX. Windows has no signals to
    send, so `os.kill` there terminates the process instead -- which is safe here because
    every write the board does is a rename into place, so a process killed mid-sentence
    leaves either the old file or the new one.
    """
    pid = read_pid(path)
    if pid is None:
        return "Nothing to stop: no pid file, so this board was not started with -d."
    if not running(pid):
        path.unlink(missing_ok=True)
        return f"Nothing to stop: pid {pid} is gone; removed the stale pid file."
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as failure:
        return f"Could not stop pid {pid}: {failure}"
    path.unlink(missing_ok=True)
    return f"Stopped pid {pid}."


def describe(path: Path, url: str) -> str:
    """One sentence about whether the board is up, and how it was started."""
    pid = read_pid(path)
    if pid is not None and running(pid):
        state = f"running as pid {pid}, from {path}"
    elif pid is not None:
        state = f"not running: pid {pid} in {path} is gone (stale pid file)"
    else:
        state = "not running: no pid file, so it was started in the foreground"
    up = "answering" if answering(url) else "not answering"
    return f"{state}; {up} at {url}"
