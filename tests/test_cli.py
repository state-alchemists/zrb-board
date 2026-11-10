"""What `zrb-board` does with the words after it on the command line.

The one that mattered: an argument it did not know started a server anyway, so
`zrb-board --version` served the board and never said which version it was.
"""

import sys

import pytest

from board import app as app_module


@pytest.fixture
def served(monkeypatch):
    """The uvicorn calls, so a test can say "started nothing" without trusting it."""
    calls = []
    monkeypatch.setattr(
        app_module.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    return calls


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["zrb-board", *argv])
    return app_module.main()


def test_should_print_the_installed_version_and_start_nothing(
    monkeypatch, capsys, served
):
    _run(monkeypatch, "--version")
    assert capsys.readouterr().out.strip() == app_module.installed_version()
    assert served == []


def test_should_refuse_an_argument_it_does_not_know(monkeypatch, capsys, served):
    with pytest.raises(SystemExit) as refused:
        _run(monkeypatch, "--serve-in-the-background")
    assert refused.value.code == 2
    assert "unknown argument" in capsys.readouterr().err
    assert served == []


def test_should_serve_when_given_no_argument(monkeypatch, served):
    _run(monkeypatch)
    assert len(served) == 1


def test_should_print_the_path_of_the_agent_side(monkeypatch, capsys, served):
    _run(monkeypatch, "init-path")
    assert capsys.readouterr().out.strip().endswith("zrb_init.py")
    assert served == []


def test_should_explain_itself_when_asked(monkeypatch, capsys, served):
    _run(monkeypatch, "--help")
    printed = capsys.readouterr().out
    assert "init-path" in printed and "--version" in printed
    assert served == []
