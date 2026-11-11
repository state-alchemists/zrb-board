"""Starting the board with no terminal, and stopping it again.

These spawn real processes, because the thing being tested is the spawn: a mock would
prove that `subprocess.Popen` was called and nothing about whether the child outlives the
caller.
"""

import os
import signal
import sys
import time

from board import app as app_module
from board import daemon


def _alive(pid, seconds=2.0):
    """Whether *pid* is still there, collecting it first where the system makes it ours.

    A child that has exited but not been collected still answers signal 0. On Linux
    `running()` sees the `Z` in `/proc` and says so; on macOS there is no `/proc`, so the
    test has to do the collecting itself -- which is the honest shape anyway: this test
    process is the parent, so reaping is its job and not the board's.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if os.name != "nt":
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    return False
            except ChildProcessError:
                return False
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


def test_should_leave_alone_a_pid_that_is_no_longer_the_board(tmp_path):
    # A pid file left by a crash can name a process that has nothing to do with the board:
    # signalling it would be worse than doing nothing, so the file records when the process
    # began and `stop` checks it before it kills anything.
    path = tmp_path / "board.pid"
    pid = daemon.detach([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "log")
    try:
        path.write_text(f"{pid}\nbegan-somewhere-else\n", encoding="utf-8")
        said = daemon.stop(path)
        assert "Refused" in said
        assert not path.exists(), "the stale file should still be cleared"
        assert daemon.running(pid), "it was not ours to kill, and it was not killed"
    finally:
        os.kill(pid, signal.SIGTERM)


def test_should_wait_for_the_process_to_go_before_reporting_stopped(tmp_path):
    # `stop && zrb-board -d` is a normal thing to type; if stop returned while the server
    # was still shutting down, the -d would find it answering and start nothing.
    path = tmp_path / "board.pid"
    pid = daemon.detach([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "log")
    daemon.write_pid(path, pid)
    said = daemon.stop(path)
    assert "Stopped" in said
    assert not daemon.running(pid), "stop reported success while the process was alive"


def test_should_refuse_a_second_start_while_one_is_starting(
    tmp_path, monkeypatch, capsys
):
    # Two `-d` at the same moment both hear "not answering" and both start a server; the
    # lock is what turns that pair of reads into one answer.
    started = []
    monkeypatch.setattr(app_module.daemon, "detach", lambda *a: started.append(a) or 1)
    monkeypatch.setattr(app_module.daemon, "answering", lambda url, timeout=1.0: False)
    monkeypatch.setattr(app_module.daemon, "read_pid", lambda path: None)
    monkeypatch.setattr(app_module.config, "home", lambda: tmp_path)
    monkeypatch.setattr(app_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["zrb-board", "-d"])

    with app_module.daemon.only_one(app_module.daemon.lock_path(tmp_path)) as held:
        assert held
        app_module.main()

    assert started == []
    assert "Another" in capsys.readouterr().out


def test_should_not_start_a_second_child_while_a_recorded_pid_still_lives(
    tmp_path, monkeypatch, capsys
):
    # A board that is slow to bind is not a board that is absent: starting a second child
    # would have the two fight for the port, and the pid file could name the loser.
    started = []
    monkeypatch.setattr(app_module.daemon, "detach", lambda *a: started.append(a) or 1)
    monkeypatch.setattr(app_module.daemon, "answering", lambda url, timeout=1.0: False)
    monkeypatch.setattr(app_module.daemon, "read_pid", lambda path: 4242)
    monkeypatch.setattr(app_module.daemon, "running", lambda pid: True)
    monkeypatch.setattr(app_module.config, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["zrb-board", "-d"])

    app_module.main()

    assert started == []
    assert "still running" in capsys.readouterr().out


def test_should_say_a_pid_belongs_to_another_process(tmp_path):
    # The process is alive -- it is this one -- but it did not begin when the file says.
    path = tmp_path / "board.pid"
    path.write_text(f"{os.getpid()}\nbegan-long-ago\n", encoding="utf-8")
    if daemon.started_at(os.getpid()) is None:
        return  # this system reports no start time; there is nothing to compare
    said = daemon.describe(path, "http://127.0.0.1:1/api/docs")
    assert "reused" in said, said
    assert "not running" in said, said


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
    assert daemon.read_pid(tmp_path / "board.pid") == 4242
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
