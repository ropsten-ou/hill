import os

import pytest


@pytest.fixture(autouse=True)
def outside_hill(monkeypatch):
    """Every test starts outside hill-ops, also when the tests run inside it, as
    they do from Claude's pane: palace would otherwise join that hill-ops
    through the $HILL_SOCKET it inherits, and its panes message would put
    the test's preview and Claude in place of yours. A test that wants a
    hill-ops sets its own."""
    for name in list(os.environ):
        if name.startswith("HILL_") or name in ("TMUX", "TMUX_PANE"):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def state_home(tmp_path, monkeypatch):
    """What palace keeps between runs, such as how Claude's sessions stand,
    goes in each test's own folder, never the real one."""
    monkeypatch.setenv("PALACE_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture(autouse=True)
def work_scan(tmp_path, monkeypatch):
    """What ~/projects/scan and sync say is each test's own: their state
    files, if a test writes them."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    return tmp_path / "xdg-state" / "work-scan.json"


@pytest.fixture(autouse=True)
def claude_config(tmp_path, monkeypatch):
    """Claude Code's folder, where palace looks for a session to carry on,
    is each test's own too, and no test runs the real Claude: a test that
    wants Claude's pane to start one gives it the stand-in, fake_claude.py."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("PALACE_CLAUDE_AGENT", "no-claude-in-tests")
    # Claude's pane sets them: recorded first, so they're undone after each test.
    for name in ("PALACE_SELECT", "PALACE_CLAUDE_KEY"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return tmp_path / "claude"
