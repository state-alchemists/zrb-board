"""Starting the board with no terminal, and stopping it again.

These spawn real processes, because the thing being tested is the spawn: a mock would
prove that `subprocess.Popen` was called and nothing about whether the child outlives the
caller.
"""

import os
import signal
import sys
import time

import pytest

from board import app as app_module
from board import daemon


def _alive(pid, seconds=2.0):
    """Whether *pid* is still there, waiting a moment for a dying child to go."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not daemon.running(pid):
            return False
        time.sleep(0.05)
    return True


def test_should_start_a_process_with_no_terminal_of_its_own(tmp_path):
    log = tmp_path / "board.log"
    pid = daemon.detach([sys.executable, "-c", "import time; time.sleep(30)"], log)
    try:
        assert daemon.running(pid)
        assert log.is_file()
    finally:
        os.kill(pid, signal.SIGTERM)
    assert not _alive(pid)


def test_should_write_the_childs_output_to_the_log(tmp_path):
    log = tmp_path / "board.log"
    pid = daemon.detach([sys.executable, "-c", "print('serving 8765')"], log)
    assert not _alive(pid)
    assert "serving 8765" in log.read_text(encoding="utf-8")


def test_should_not_call_a_pid_that_is_gone_running(tmp_path):
    pid = daemon.detach([sys.executable, "-c", "pass"], tmp_path / "log")
    assert not _alive(pid)
    assert not daemon.running(pid)
    assert not daemon.running(0)
    assert not daemon.running(-1)


def test_should_read_back_a_pid_and_survive_a_broken_file(tmp_path):
    path = tmp_path / "board.pid"
    assert daemon.read_pid(path) is None
    path.write_text("4242\n", encoding="utf-8")
    assert daemon.read_pid(path) == 4242
    path.write_text("not a number", encoding="utf-8")
    assert daemon.read_pid(path) is None


def test_should_say_there_is_nothing_to_stop_without_a_pid_file(tmp_path):
    said = daemon.stop(tmp_path / "board.pid")
    assert "nothing to stop" in said.lower()


def test_should_remove_a_stale_pid_file_and_say_so(tmp_path):
    path = tmp_path / "board.pid"
    pid = daemon.detach([sys.executable, "-c", "pass"], tmp_path / "log")
    assert not _alive(pid)
    path.write_text(str(pid), encoding="utf-8")
    said = daemon.stop(path)
    assert "stale" in said
    assert not path.exists()


def test_should_stop_a_running_child(tmp_path):
    path = tmp_path / "board.pid"
    pid = daemon.detach([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "log")
    path.write_text(str(pid), encoding="utf-8")
    assert "stopped" in daemon.stop(path).lower()
    assert not path.exists()
    assert not _alive(pid)


def test_should_describe_a_board_that_is_not_there(tmp_path):
    said = daemon.describe(tmp_path / "board.pid", "http://127.0.0.1:1/api/docs")
    assert "not running" in said
    assert "not answering" in said


def test_should_start_a_detached_copy_and_write_its_pid(tmp_path, monkeypatch, capsys):
    started = []
    asked = []

    def fake_detach(command, log):
        started.append((command, log))
        return 4242

    def answering(url, timeout=1.0):
        asked.append(url)
        return len(asked) > 1  # not up before the start, up after it

    monkeypatch.setattr(app_module.daemon, "detach", fake_detach)
    monkeypatch.setattr(app_module.daemon, "answering", answering)
    monkeypatch.setattr(app_module.daemon, "running", lambda pid: True)
    monkeypatch.setattr(app_module.config, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["zrb-board", "-d"])

    app_module.main()

    assert started and started[0][0][-2:] == ["-m", "board.app"]
    assert (tmp_path / "board.pid").read_text(encoding="utf-8") == "4242"
    assert "Serving in the background as pid 4242" in capsys.readouterr().out


def test_should_not_start_a_second_board_when_one_answers(tmp_path, monkeypatch, capsys):
    started = []
    monkeypatch.setattr(app_module.daemon, "detach", lambda *a: started.append(a) or 1)
    monkeypatch.setattr(app_module.daemon, "answering", lambda url, timeout=1.0: True)
    monkeypatch.setattr(app_module.config, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["zrb-board", "-d"])

    app_module.main()

    assert started == []
    assert "Already serving" in capsys.readouterr().out
