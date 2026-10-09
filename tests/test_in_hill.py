"""palace inside hill-ops, for real: hill-ops runs in a pseudo-terminal with its own
tmux server, and the test drives palace and hill-ops's strip through tmux."""

import fcntl
import json
import os
import pty
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")


class Terminal:
    """A command in a pseudo-terminal of a given size, its output drained."""

    def __init__(self, argv, env, cols, rows):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execvpe(argv[0], argv, env)
        self.resize(cols, rows)
        self.output = bytearray()
        threading.Thread(target=self._drain, daemon=True).start()

    def resize(self, cols, rows):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def _drain(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.output.extend(data)

    def wait(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                time.sleep(0.2)
                return os.waitstatus_to_exitcode(status)
            time.sleep(0.05)
        os.kill(self.pid, 9)
        raise AssertionError("hill-ops didn't exit")


def until(check, what, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if result := check():
            return result
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def session(tmp_path):
    vault = tmp_path / "vault"
    (vault / "projects").mkdir(parents=True)
    (vault / "Root.md").write_text("---\ntitle: Root\n---\nStart here.\n\n[[Projects]]\n")
    (vault / "Projects.md").write_text("# Local projects\n\n[[garden]]\n")
    (vault / "projects" / "garden.md").write_text("# garden\n\nListed in [[Projects]].\n")
    git(vault, "init", "-q")
    git(vault, "config", "user.name", "t")
    git(vault, "config", "user.email", "t@t")
    git(vault, "commit", "-q", "--allow-empty", "-m", "start")
    name = f"hill-palace-test-{os.getpid()}"
    run_dir = Path(tempfile.mkdtemp(prefix="wp", dir="/tmp"))  # socket paths must be short
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "HILL_SOCKET")}
    env.update(
        TERM="xterm-256color", PALACE_VAULT=str(vault), PALACE_CONFIG_HOME=str(tmp_path / "palace"),
        MICRO_CONFIG_HOME=str(tmp_path / "micro"), HILL_CONFIG_HOME=str(tmp_path / "hill"),
        HILL_STATE_HOME=str(tmp_path / "state"), HILL_RUNTIME_DIR=str(run_dir), HILL_TMUX_NAME=name,
        HILL_CLAUDE_AGENT="no-claude-in-tests", PALACE_CLAUDE_AGENT="no-claude-in-tests",
    )

    def tmux(*args):
        return subprocess.run(["tmux", "-L", name, *args], capture_output=True, text=True).stdout

    yield env, tmux, run_dir
    tmux("kill-server")
    (Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}" / name).unlink(missing_ok=True)
    shutil.rmtree(run_dir, ignore_errors=True)


def test_palace_starts_in_hill_with_its_preview_and_claude_beside_it_and_the_strip_under_them(session, tmp_path):
    env, tmux, run_dir = session
    # Plain `palace`, which starts itself inside hill-ops (found next to this Python).
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"
    env["PALACE_CLAUDE_AGENT"] = f"{sys.executable} {Path(__file__).with_name('mouse_claude.py')}"
    terminal = Terminal([sys.executable, "-m", "hill"], env, 100, 30)

    def panes(count=None):
        rows = tmux("list-panes", "-t", "hill", "-F", "#{pane_id} #{pane_left} #{pane_top} #{pane_width} #{pane_height} #{pane_active}")
        found = {pane: tuple(map(int, rest)) for pane, *rest in (line.split() for line in rows.splitlines())}
        return found if count is None or len(found) == count else None

    def screen(pane):
        return tmux("capture-pane", "-p", "-t", pane)

    app, strip = until(lambda: panes(2), "palace and the strip")
    until(lambda: "shell" in screen(strip) and "quit" in screen(strip), "palace's keys on the strip")
    until(lambda: "garden" in screen(app), "the notes")
    assert "settings" not in screen(app)  # left the panes' key lines for the strip

    # The preview and Claude join beside palace, which keeps the window's height; the strip goes under them.
    preview, claude = until(lambda: sorted((p for p in panes(4) or {} if p not in (app, strip)), key=lambda p: panes()[p][0]) or None,
                            "the preview's pane and Claude's")
    until(lambda: "Listed in" in screen(preview), "the note selected, in the preview")
    until(lambda: "Claude on vault" in screen(claude) and "It starts a new session" in screen(claude),
          "Claude's pane on the note's session, waiting for Ret")
    assert panes()[app] == (0, 0, 25, 30, 1)
    assert panes()[preview][:4] == (26, 0, 43, 28) and panes()[claude][:4] == (70, 0, 30, 28)
    assert panes()[strip][:4] == (26, 29, 74, 1)
    assert "Listed in" not in screen(app)  # palace's own preview hides

    def move(*cells):  # the mouse moving over the window, as the terminal says
        for x, y in cells:
            os.write(terminal.fd, f"\x1b[<35;{x + 1};{y + 1}M".encode())
            time.sleep(0.05)

    os.write(terminal.fd, b"\x1b[<0;85;11M\x1b[<0;85;11m")  # Claude's pane, which doesn't watch the mouse yet, takes a click
    until(lambda: panes()[claude][4] == 1, "Claude's pane focused by a click")
    tmux("send-keys", "-t", claude, "Enter")  # Claude starts, and watches the mouse, as `claude` does fullscreen
    until(lambda: "watching the mouse" in screen(claude), "Claude started")
    move((10, 10), (6, 10), (2, 11))  # and the mouse back into palace's
    until(lambda: panes()[app][4] == 1, "palace focused by the mouse")
    move((80, 10), (84, 11), (88, 12))  # and into Claude's: hill-ops's relay takes the focus for it
    until(lambda: panes()[claude][4] == 1, "Claude's pane focused by the mouse")
    move((10, 10), (6, 10), (2, 11))
    until(lambda: panes()[app][4] == 1, "palace focused by the mouse again")

    tmux("send-keys", "-t", app, "Tab", "Tab")  # past the calendar, to the preview's pane
    until(lambda: panes()[preview][4] == 1, "the preview focused")
    tmux("send-keys", "-t", preview, ",")  # the settings, on the preview's group, under it
    until(lambda: panes()[strip][3:] == (10, 1), "the strip grown and focused")
    until(lambda: "Show the selected note" in screen(strip), "the Preview group")
    until(lambda: "can't start" in screen(strip) and "ask Claude, or : for palace's commands" in screen(strip), "Claude's lines")

    tmux("send-keys", "-t", strip, "Enter")  # Preview: on -> off
    prefs = tmp_path / "palace" / "settings.json"
    until(lambda: prefs.exists() and json.loads(prefs.read_text()) == {"notes": {"preview": False}}, "the setting saved")
    until(lambda: panes(3), "the preview's pane gone")
    assert panes()[claude][:4] == (26, 0, 74, 19)  # Claude takes its place, over the panel, still grown
    assert panes()[strip][:4] == (26, 20, 74, 10)

    terminal.resize(120, 40)
    until(lambda: panes()[strip][3] == 13, "the strip taking its share of the new height")
    until(lambda: any('"resize"' in log.read_text() for log in (tmp_path / "state" / "events").glob("*.jsonl")),
          "the new size in hill-ops's event log, once it has lasted")

    tmux("send-keys", "-t", strip, "Escape")
    until(lambda: panes()[strip][3:] == (1, 0) and panes()[app][4] == 1, "the strip shrunk, palace focused")

    tmux("send-keys", "-t", app, ":")
    until(lambda: panes()[strip][3:] == (13, 1) and "rename" in screen(strip), "the panel as the command line, with palace's commands")
    move((12, 5), (16, 6), (20, 7))  # the mouse over palace doesn't take the focus from the strip
    time.sleep(0.5)
    assert panes()[strip][3:] == (13, 1)
    tmux("send-keys", "-t", strip, "search ga", "Enter")
    until(lambda: "garden" in screen(app) and "Root" not in screen(app), "only garden listed")
    until(lambda: panes()[strip][3:] == (1, 0), "the strip back")
    tmux("send-keys", "-t", app, "Escape")  # all notes again
    until(lambda: "Root" in screen(app), "all notes")

    tmux("send-keys", "-t", app, "n")
    until(lambda: "New note title" in screen(strip) and panes()[strip][4] == 1, "the question on the strip")
    tmux("send-keys", "-t", strip, "Ideas", "Enter")

    note = Path(env["PALACE_VAULT"]) / "projects" / "Ideas.md"  # in the folder under the cursor

    def micro_open():
        return subprocess.run(["pgrep", "-f", f"micro .*{note}"], capture_output=True).returncode == 0

    until(micro_open, "the new note open in micro")
    micro = until(lambda: next((p for p in panes(3) or {} if p not in (app, strip, claude)), None), "micro beside palace")
    assert panes()[micro][:2] == (31, 0) and "garden" in screen(app)  # over Claude's pane; palace stays in sight

    def quit_micro():  # a key sent while micro is still starting can be lost
        tmux("send-keys", "-t", micro, "C-q")
        time.sleep(0.5)
        return not micro_open()

    until(quit_micro, "micro closed")
    until(lambda: set(panes(3) or ()) == {app, claude, strip} and "Ideas" in screen(app), "Claude back, palace on the new note")

    tmux("send-keys", "-t", app, "q")
    assert terminal.wait() == 0
    after_tmux = terminal.output.decode(errors="replace").rsplit("\x1b[?1049l", 1)[-1]
    assert "palace: committed 4 changes" in after_tmux
    assert "[detached" not in after_tmux
    assert list(run_dir.glob("*/channel.sock")) == []

    # hill-ops's event log: what palace did through the strip, without the search or the title.
    (log,) = (tmp_path / "state" / "events").glob("*.jsonl")
    events = [json.loads(line) for line in log.read_text().splitlines()]
    # The strip may be stopped before it hears palace leave, and Claude's
    # failing to start for the panel's tip comes when it comes. Claude's
    # pane comes in a second panes, once the note has stayed selected.
    assert [e["event"] for e in events if e["event"] not in ("bye", "problem")] == [
        "start", "hello", "panes", "panes", "settings.open", "settings.changed", "panes", "resize", "close",
        "command.open", "command", "close", "ask", "answer", "close", "over", "over.done", "end",
    ]
    assert events[0]["command"] == "palace"
    assert {e["app"] for e in events if e["event"] not in ("start", "end")} == {"palace"}
    assert "cb" not in log.read_text() and "Ideas" not in log.read_text()
