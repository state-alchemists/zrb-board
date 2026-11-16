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

import errno
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PID_NAME = "board.pid"
LOG_NAME = "board.log"
LOCK_NAME = "board.lock"

# What a Windows handle has to be allowed to do to be of use to `stop`: say when the process
# began (`PROCESS_QUERY_LIMITED_INFORMATION`) and end it (`PROCESS_TERMINATE`). `STILL_ACTIVE`
# is the exit code a process that has not exited reports.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259


def pid_path(home: Path) -> Path:
    return home / PID_NAME


def lock_path(home: Path) -> Path:
    return home / LOCK_NAME


@contextmanager
def only_one(path: Path) -> Iterator[bool]:
    """Hold a lock while starting, so two `-d` cannot both spawn a server.

    The check "is it answering?" is a read; two commands that read it at the same moment
    both get "no" and both start one. This is the write that separates them. The lock is
    advisory and held only for the start -- the answer says False rather than waiting, since
    the other command is doing the same job.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        if not _lock(handle):
            yield False
            return
        try:
            yield True
        finally:
            _unlock(handle)


def _lock(handle) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass  # closing the file releases it on POSIX anyway


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

    Signal 0 does not deliver anything on POSIX; it only asks whether it could. Windows is
    the exception that matters: `os.kill` there terminates the process whatever the number,
    so asking "are you alive" that way would be answering it. `OpenProcess` asks instead.

    An exited process whose parent has not reaped it answers signal 0 until it is collected,
    so Linux's `/proc` is asked the second question.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _running_on_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return _not_a_zombie(pid)


def _kernel32():
    """Windows' kernel32, with the signatures this module relies on declared.

    ctypes assumes a function returns `c_int`, and on 64-bit Windows a HANDLE does not fit
    in one: the call succeeds and everything after it operates on a truncated handle, so a
    live process reads as dead. Declaring them is not tidiness here, it is correctness.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    return ctypes, wintypes, kernel32


def _running_on_windows(pid: int) -> bool:
    """Ask Windows whether a process is still running, without touching it.

    `os.kill(pid, 0)` on Windows calls TerminateProcess with 0 -- it kills. `OpenProcess`
    fails for a pid that is gone, and `GetExitCodeProcess` separates a live process from a
    dead one whose object another handle is still holding open.
    """
    ctypes, wintypes, kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


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
    """The pid in the first line of *path*, or None when it is missing or not a number."""
    line = (read_lines(path) or [""])[0]
    try:
        return int(line.strip())
    except ValueError:
        return None


def read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None


def read_identity(path: Path) -> str | None:
    """The start time written beside the pid, so a reused pid is not mistaken for it."""
    lines = read_lines(path) or []
    return lines[1].strip() if len(lines) > 1 and lines[1].strip() else None


def started_at(pid: int) -> str | None:
    """When the process began, as the system reports it, or None where it cannot be read.

    A pid is reused: the number in a file left behind by a crash can belong to something
    else entirely by the time `stop` reads it, and signalling that would be worse than
    leaving the board alone. Linux gives this in `/proc`, macOS through `ps`, Windows
    through the process's own creation time.
    """
    if os.name == "nt":
        return _windows_started_at(pid)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    else:
        fields = stat.rsplit(")", 1)[-1].split()
        if len(fields) > 19:
            return fields[19]
    try:
        answered = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
        ).stdout.strip()
    except OSError:
        return None
    return answered or None


def _windows_started_at(pid: int) -> str | None:
    """The process's creation time, so a reused pid can be told from the original."""
    kernel32 = _kernel32()[2]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        return _created_at(handle)
    finally:
        kernel32.CloseHandle(handle)


def _created_at(handle) -> str | None:
    """When the process behind *handle* began -- asked of the handle, not of the number."""
    ctypes, wintypes, kernel32 = _kernel32()
    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    asked = kernel32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    )
    if not asked:
        return None
    return str((created.dwHighDateTime << 32) | created.dwLowDateTime)


class _Pinned:
    """A process held open, so what is checked and what is signalled are the same object.

    `stop` reads when the process began, compares that with what the pid file recorded, and
    only then signals -- three separate looks at a *number*, with the process free to exit and
    the number free to be handed to something else between any two of them. Holding the
    process first is what closes that: the signal travels through the hold, and a hold names
    a process rather than a number, so it cannot reach whoever took the number over.

    The identity is still read by number, and that read can now be wrong in one direction
    only. Once the held process is gone, its number may answer for its successor; the
    comparison then refuses to stop, which is the safe answer, and nothing is signalled.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def identity(self) -> str | None:
        """When the held process began, as `write_pid` would have recorded it."""
        return started_at(self.pid)

    def terminate(self) -> None:
        os.kill(self.pid, signal.SIGTERM)

    def close(self) -> None:
        """Let go. Where the hold is only the number, there is nothing to release."""


class _PinnedByFd(_Pinned):
    """Linux: a pidfd, which refers to the process rather than to its number."""

    def __init__(self, pid: int, fd: int) -> None:
        super().__init__(pid)
        self.fd = fd

    def terminate(self) -> None:
        signal.pidfd_send_signal(self.fd, signal.SIGTERM)

    def close(self) -> None:
        os.close(self.fd)


class _PinnedByHandle(_Pinned):
    """Windows: one handle, opened once, asked and ended through.

    It is opened for both jobs at once -- to read the creation time and to end the process --
    because a handle opened later for the kill would be a second lookup of the number, and a
    second lookup can name a second process. `os.kill` opens one of its own.
    """

    def __init__(self, pid: int, handle) -> None:
        super().__init__(pid)
        self.handle = handle

    def identity(self) -> str | None:
        return _created_at(self.handle)

    def terminate(self) -> None:
        ctypes, _, kernel32 = _kernel32()
        # The exit code is the signal number, which is what `os.kill` on Windows handed to
        # `TerminateProcess`: the same death, through the handle that was already checked.
        if not kernel32.TerminateProcess(self.handle, signal.SIGTERM):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        _kernel32()[2].CloseHandle(self.handle)


def _pinned(pid: int) -> _Pinned:
    """Hold *pid*'s process in whatever way this system offers.

    - **Linux** has `pidfd_open`, and `pidfd_send_signal` to signal through the descriptor it
      returns. Both are stdlib since Python 3.9 and need Linux 5.3; a kernel without them
      answers `ENOSYS`, and the number is all that is left. The issue behind this was written
      while neither was exposed to Python, when the two syscalls had to be made by hand.
    - **Windows** has `OpenProcess`, a handle that keeps the process object alive and names
      it, so the creation time and the kill are asked of one handle.
    - **macOS** has neither: `proc_pidinfo` and `kill` are two lookups of the same number,
      with nothing to show they are the same process. `stop` does the comparison and the
      signal back to back, which narrows the window and does not close it. Closing it would
      take a pidfd, and Darwin has none.

    Raises `ProcessLookupError` when the process has already gone -- a pid file left by a
    crash -- and any other `OSError` it cannot work around, such as a full descriptor table.
    `stop` reports both rather than falling back to a signal it cannot vouch for.
    """
    if os.name == "nt":
        return _hold_on_windows(pid)
    fd = _pidfd(pid)
    return _Pinned(pid) if fd is None else _PinnedByFd(pid, fd)


def _pidfd(pid: int) -> int | None:
    """A descriptor referring to *pid*'s process, or None where this system has none."""
    open_fd = getattr(os, "pidfd_open", None)
    if open_fd is None or not hasattr(signal, "pidfd_send_signal"):
        return None
    try:
        return open_fd(pid)
    except OSError as refusal:
        if refusal.errno == errno.ENOSYS:
            return None  # a kernel older than 5.3: it has no pidfd to give
        raise


def _hold_on_windows(pid: int) -> _PinnedByHandle:
    """Open the one handle `stop` will ask and end through.

    A handle that will not open means one of two things, and they are worth telling apart:
    the process is gone, or it is there and not ours to end. Separating them costs one more
    handle, and only on the way out -- but saying "gone" about a board that is still serving
    would have `stop` delete a pid file it should have left alone.
    """
    kernel32 = _kernel32()[2]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False, pid)
    if handle:
        return _PinnedByHandle(pid, handle)
    if _running_on_windows(pid):
        raise PermissionError(errno.EACCES, f"not allowed to stop pid {pid}")
    raise ProcessLookupError(errno.ESRCH, f"no process {pid}")


def write_pid(path: Path, pid: int) -> None:
    """Write the pid, and what the system says its process began, for `stop` to check."""
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = started_at(pid) or ""
    path.write_text(f"{pid}\n{identity}\n", encoding="utf-8")


def answering(url: str, timeout: float = 1.0) -> bool:
    """Whether something at *url* answers at all -- the cheapest "is the board up"."""
    try:
        urllib.request.urlopen(url, timeout=timeout).close()
    except (urllib.error.URLError, OSError):
        return False
    return True


def _forget(path: Path, pid: int) -> None:
    """Remove the pid file, unless something else has already claimed it.

    `stop` and `-d` take the same lock now, so this is belt and braces -- but a file that
    names a different pid is not ours to delete, whoever wrote it.
    """
    if read_pid(path) == pid:
        path.unlink(missing_ok=True)


def _gone(path: Path, pid: int) -> str:
    """Forget a pid file whose process is not there, and say so."""
    _forget(path, pid)
    return f"Nothing to stop: pid {pid} is gone; removed the stale pid file."


def stop(path: Path) -> str:
    """Stop the process named in *path*, and say what happened.

    SIGTERM, which uvicorn turns into a clean shutdown on POSIX. Windows has no signals to
    send, so the process is ended with `TerminateProcess` instead -- which is safe here
    because every write the board does is a rename into place, so a process ended mid-sentence
    leaves either the old file or the new one.

    Two things this does that a bare `kill` does not. It waits for the process to go, so
    `stop && zrb-board -d` starts a board instead of finding the dying one still answering;
    and it checks that the pid is still the process the file was written for, because a pid
    left behind by a crash can be someone else's by the time this runs.

    A check is only worth making if the signal cannot land anywhere else, so the process is
    held first and signalled through the hold (`_pinned`): a pidfd on Linux, one handle on
    Windows. **On macOS there is nothing to hold with** -- `proc_pidinfo` and `kill` are two
    lookups of the same number -- so the comparison and the signal are done back to back, and
    a window microseconds wide remains in which the process exits, the number is reused, and
    the signal reaches whatever took it. That gap cannot be closed without a pidfd, which
    Darwin does not have.
    """
    pid = read_pid(path)
    if pid is None:
        return "Nothing to stop: no pid file, so this board was not started with -d."
    if not running(pid):
        return _gone(path, pid)
    expected = read_identity(path)
    if not expected:
        # No identity was recorded -- the child may have died before it could be read --
        # so there is nothing proving this pid is the board. Refusing to guess is the
        # only safe answer: a wrong guess kills whatever now holds that number.
        path.unlink(missing_ok=True)
        return (
            f"Refused: pid {pid} is alive but this file records no start time for it, so "
            "nothing proves it is the board. It was not signalled; stop it by hand if you "
            "are sure."
        )
    refused = _ended(path, pid, expected)
    if refused is not None:
        return refused
    for _ in range(100):
        if not running(pid) or _collected(pid):
            break
        time.sleep(0.05)
    if running(pid):
        return f"Signalled pid {pid}, but it is still running; left its pid file in place."
    _forget(path, pid)
    return f"Stopped pid {pid}."


def _ended(path: Path, pid: int, expected: str) -> str | None:
    """Hold the process, check it is still the one the file names, and signal it.

    None means the signal went through a hold, which proves it reached the process the file
    was written for. Anything else is the sentence `stop` reports instead of waiting.
    """
    try:
        held = _pinned(pid)
    except ProcessLookupError:
        return _gone(path, pid)
    except OSError as failure:
        return f"Could not stop pid {pid}: {failure}"
    try:
        current = held.identity()
        if current and expected != current:
            path.unlink(missing_ok=True)
            return (
                f"Refused: pid {pid} began at {current}, and this file was written for the "
                "process that began at "
                f"{expected}. Nothing was signalled; the stale pid file was removed."
            )
        held.terminate()
    except OSError as failure:
        return f"Could not stop pid {pid}: {failure}"
    finally:
        held.close()
    return None


def _collected(pid: int) -> bool:
    """Whether *pid* is a child of this process that has exited, and is now reaped.

    Only a test has a board as its child, but there the wait would otherwise never end: on
    macOS a zombie answers signal 0, and there is no `/proc` to notice. `waitpid` is the
    question that settles it, and it can only be asked about our own children.
    """
    if os.name == "nt":
        return False
    try:
        return bool(os.waitpid(pid, os.WNOHANG)[0])
    except (ChildProcessError, OSError):
        return False


def describe(path: Path, url: str) -> str:
    """One sentence about whether the board is up, and how it was started."""
    pid = read_pid(path)
    identity = read_identity(path)
    current = started_at(pid) if pid is not None else None
    if pid is not None and identity and current and identity != current:
        state = (
            f"not running: pid {pid} in {path} began at {current}, not at {identity} -- "
            "the pid was reused, so the file is stale"
        )
    elif pid is not None and running(pid):
        state = f"running as pid {pid}, from {path}"
    elif pid is not None:
        state = f"not running: pid {pid} in {path} is gone (stale pid file)"
    else:
        state = "not running: no pid file, so it was started in the foreground"
    up = "answering" if answering(url) else "not answering"
    return f"{state}; {up} at {url}"
