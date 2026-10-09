"""Claude's pane on its own: the program hill-ops runs for each session, which
becomes `claude` once you press Ret (here a stand-in, fake_claude.py), and
the hooks that say how each session stands."""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hill import claude
from hill.config import state_home

FAKE = Path(__file__).with_name("fake_claude.py")


@pytest.fixture
def item(tmp_path):
    work = tmp_path / "vault" / "projects" / "work"
    work.mkdir(parents=True)
    path = work / "001-ask.md"
    path.write_text("---\nstatus: doing  # kept\nsince: 2026-10-06\n---\n# Ask\n")
    return path


def pane(key, cwd, typed, tmp_path, done=False, mirror=None):
    """Run a session's program as hill-ops would, typing `typed`; what it showed,
    and what the stand-in for claude was started with, if it was."""
    log = tmp_path / "claude.json"
    log.unlink(missing_ok=True)
    env = {**os.environ, "FAKE_CLAUDE_LOG": str(log), "TMUX_PANE": "%5"}
    argv = [sys.executable, "-m", "hill", "_claude", str(key), str(cwd), str(state_home()),
            *(["--done"] if done else []), *(["--mirror", mirror] if mirror else []), sys.executable, str(FAKE)]
    done = subprocess.run(argv, input=typed, capture_output=True, text=True, env=env, timeout=30)
    return done.stdout, json.loads(log.read_text()) if log.exists() else None


def test_a_sessions_program_waits_for_ret_then_becomes_claude_on_a_new_session(item, tmp_path):
    cwd = item.parents[1]
    shown, started = pane(item, cwd, "what now\n", tmp_path)
    assert "Claude on projects 001" in shown and "It starts a new session." in shown
    session = claude.item_session(str(item))
    assert session and len(session) == 36  # an id palace picked, written before Claude started
    assert "status: doing  # kept" in item.read_text()  # nothing else changes
    settings = str(state_home() / "claude" / "settings.json")
    assert started["argv"] == ["--settings", settings, "--plugin-dir", str(claude.MOD),
                               "--session-id", session, "what now"]
    assert started["cwd"] == str(cwd)
    assert started["env"] == {"PALACE_CLAUDE_KEY": str(item), "PALACE_SELECT": str(claude.select_file(str(item))),
                              "PALACE_STATE_HOME": str(state_home())}
    given = json.loads(Path(settings).read_text())
    assert given["tui"] == "fullscreen"  # Claude watches the mouse, so hill-ops gives its pane the focus
    hooks = given["hooks"]
    assert sorted(hooks) == sorted(claude.HOOKS)
    assert hooks["Stop"][0]["hooks"][0]["command"].endswith("-m hill _hook")
    # palace knows its pane before Claude starts.
    assert claude.state_of(str(item))["pane"] == "%5" and claude.state_of(str(item))["state"] is None


def test_it_carries_on_the_session_the_item_names_if_claude_has_it(item, tmp_path):
    cwd = item.parents[1]
    item.write_text(item.read_text().replace("since:", "session: s1\nsince:"))
    shown, started = pane(item, cwd, "\n", tmp_path)
    assert "It starts a new session." in shown  # Claude Code doesn't have s1
    assert started["argv"][4] == "--session-id" and claude.item_session(str(item)) != "s1"
    item.write_text(item.read_text().replace(f"session: {claude.item_session(str(item))}", "session: s1"))
    transcript = claude.transcript(str(cwd), "s1")
    assert transcript.parent.name == str(cwd).replace("/", "-").replace("_", "-").replace(".", "-")
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    shown, started = pane(item, cwd, "\n", tmp_path)
    assert "It carries on session s1" in shown
    assert started["argv"][4:] == ["--resume", "s1"]  # Ret alone: no first question
    assert claude.item_session(str(item)) == "s1"


def reply(transcript, ago, tokens, life="1h"):
    """A transcript whose last reply was `ago` seconds back, with `tokens` in
    its context, written to the cache of `life`."""
    from datetime import datetime, timezone
    at = datetime.fromtimestamp(time.time() - ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    made = {"ephemeral_1h_input_tokens": 100 if life == "1h" else 0, "ephemeral_5m_input_tokens": 100 if life == "5m" else 0}
    usage = {"input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": tokens - 102,
             "cache_creation": made}
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({"type": "user", "timestamp": at}) + "\n"
                          + json.dumps({"type": "assistant", "timestamp": at, "message": {"usage": usage}}) + "\n"
                          + json.dumps({"type": "last-prompt"}) + "\n")


@pytest.mark.parametrize("ago, tokens, life, resumes", [
    (30 * 60, 280_000, "1h", True),    # warm
    (90 * 60, 280_000, "1h", False),   # cold and big: a new session on the item
    (90 * 60, 40_000, "1h", True),     # cold but small
    (10 * 60, 280_000, "5m", False),   # in overage the cache lives 5 minutes
])
def test_a_cold_session_starts_anew_on_the_items_file(item, tmp_path, ago, tokens, life, resumes):
    cwd = item.parents[1]
    item.write_text(item.read_text().replace("since:", "session: s1\nnext: 'write the tests'\nsince:"))
    reply(claude.transcript(str(cwd), "s1"), ago, tokens, life)
    assert claude.cache(claude.transcript(str(cwd), "s1"))[1:] == (300.0 if life == "5m" else 3600.0, tokens)
    shown, started = pane(item, cwd, "and the docs\n", tmp_path)
    if resumes:
        assert "It carries on session s1" in shown and ("warm until" in shown or "cold since" in shown)
        assert started["argv"][4:] == ["--resume", "s1", "and the docs"]
        return
    assert "Session s1 went cold at" in shown and "280k tokens" in shown and "new session on the item" in shown
    session = claude.item_session(str(item))
    assert session != "s1"
    assert started["argv"][4:] == ["--session-id", session, "Carry on work/001-ask.md from its file; "
                                   "next: write the tests\n\nand the docs"]
    # r resumes it all the same.
    item.write_text(item.read_text().replace(f"session: {session}", "session: s1"))
    _, started = pane(item, cwd, "r\n", tmp_path)
    assert started["argv"][4:] == ["--resume", "s1"]


def test_a_projects_cold_session_still_resumes(tmp_path):
    cwd = tmp_path / "vault" / "garden"
    cwd.mkdir(parents=True)
    claude.write_session(str(cwd), "s1")
    reply(claude.transcript(str(cwd), "s1"), 3 * 3600, 280_000)
    shown, started = pane(cwd, cwd, "\n", tmp_path)  # it has no file to start anew from
    assert "cold since" in shown and started["argv"][4:] == ["--resume", "s1"]


def test_a_projects_notes_share_its_session(tmp_path):
    cwd = tmp_path / "vault" / "garden"
    cwd.mkdir(parents=True)
    _, started = pane(cwd, cwd, "hello\n", tmp_path)
    session = started["argv"][5]
    assert claude.session_of(str(cwd)) == session  # kept in palace's state folder
    assert json.loads((state_home() / "claude" / "sessions.json").read_text()) == {str(cwd): session}


def test_ctrl_d_closes_the_pane_before_claude_starts(item, tmp_path):
    shown, started = pane(item, item.parents[1], "", tmp_path)
    assert started is None and claude.item_session(str(item)) is None
    assert not claude.state_file(str(item)).exists()


def hook(monkeypatch, capsys, event, **fields):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": event, **fields})))
    claude.hook()
    assert capsys.readouterr().out == ""  # what a SessionStart hook prints goes to Claude


def test_the_hooks_say_how_the_session_stands(item, monkeypatch, capsys):
    key = str(item)
    hook(monkeypatch, capsys, "SessionStart", session_id="s1", source="startup")
    assert not claude.state_file(key).exists()  # not a session palace started
    monkeypatch.setenv(claude.KEY, key)
    monkeypatch.setenv("TMUX_PANE", "%5")
    states = []
    for event, fields in [("SessionStart", {"source": "startup"}), ("UserPromptSubmit", {}),
                          ("Notification", {"notification_type": "permission_prompt"}),
                          ("Notification", {"notification_type": "idle_prompt"}), ("PostToolUse", {}), ("Stop", {})]:
        hook(monkeypatch, capsys, event, session_id="s1", **fields)
        states.append(claude.read_states()[key]["state"])
    assert states == ["idle", "answering", "asks", "asks", "answering", "idle"]
    assert claude.read_states()[key]["pane"] == "%5"
    # `since` says when the state began, for the list's timer: an event that
    # leaves it as it was, such as a second Stop, keeps it.
    since = claude.read_states()[key]["since"]
    hook(monkeypatch, capsys, "Stop", session_id="s1")
    assert claude.read_states()[key]["since"] == since
    assert claude.item_session(key) == "s1"  # a session it didn't name, such as after /clear, is written there
    hook(monkeypatch, capsys, "SessionEnd", session_id="s1")
    assert claude.read_states() == {}


def test_the_hook_tells_your_questions_from_palaces_write_back(item, monkeypatch, capsys):
    key = str(item)
    monkeypatch.setenv(claude.KEY, key)
    hook(monkeypatch, capsys, "UserPromptSubmit", session_id="s1", prompt="go")
    asked = claude.read_states()[key]["asked"]
    assert "wrote_back" not in claude.read_states()[key]
    hook(monkeypatch, capsys, "UserPromptSubmit", session_id="s1", prompt=claude.WRITE_BACK)
    entry = claude.read_states()[key]
    assert entry["asked"] == asked and entry["wrote_back"] >= asked


def test_a_session_wants_the_write_back_once_idle_a_while_after_your_question():
    now = 100_000.0
    idle = {"state": "idle", "asked": now - 1800, "since": now - claude.WRITE_BACK_AFTER}
    assert claude.wants_write_back(idle, None, now)
    assert claude.wants_write_back(idle, now - 3600, now)  # committed before the question
    assert not claude.wants_write_back(idle, now - 1700, now)  # committed since: written back
    assert not claude.wants_write_back(idle | {"wrote_back": now - 1000}, None, now)  # asked for once
    assert claude.wants_write_back(idle | {"wrote_back": now - 2000}, None, now)  # for an earlier question
    assert not claude.wants_write_back(idle | {"since": now - 60}, None, now)  # not idle long enough
    assert not claude.wants_write_back(idle | {"state": "asks"}, None, now)  # waits on you
    assert not claude.wants_write_back({"state": "idle", "since": now - 3600}, None, now)  # you asked nothing


def test_two_hills_never_see_each_others_sessions(item, tmp_path, monkeypatch, capsys):
    key, cwd = str(item), str(item.parents[1])
    monkeypatch.setenv(claude.KEY, key)
    monkeypatch.setenv("HILL_RUN", str(tmp_path / "hill-1"))
    hook(monkeypatch, capsys, "SessionStart", session_id="s1", source="startup")
    claude.keep_shown(key, cwd)
    claude.select_file(key).write_text("README.md\n")
    claude.write_session(cwd, "p1")  # a project's last session outlives hill-ops
    assert list(claude.read_states()) == [key] and claude.shown() == (key, cwd)
    monkeypatch.setenv("HILL_RUN", str(tmp_path / "hill-2"))
    assert claude.read_states() == {} and claude.shown() is None
    assert claude.selected(key, cwd) is None  # the other palace's selection stays its own
    assert claude.session_of(cwd) == "p1" and claude.item_session(key) == "s1"
    monkeypatch.setenv("HILL_RUN", str(tmp_path / "hill-1"))
    assert claude.selected(key, cwd) == str(Path(cwd) / "README.md")


def test_on_an_item_done_and_pushed_it_says_how_to_carry_it_on_and_starts_nothing(item, tmp_path):
    cwd = item.parents[1]
    shown, started = pane(item, cwd, "it doesn't work\n\n", tmp_path, done=True)
    assert "It's done and pushed" in shown and "reopen it (status: doing)" in shown
    assert "Type a question" not in shown
    assert started is None and claude.item_session(str(item)) is None
    assert claude.read_states() == {}  # Ctrl-d: forgotten, as before Claude starts


def test_on_a_mirrors_note_it_says_where_the_project_is_worked_on_and_starts_nothing(item, tmp_path):
    cwd = item.parents[1]
    shown, started = pane(item, cwd, "what now\n\n", tmp_path, mirror="build-box")
    assert "worked on build-box" in shown and "ssh build-box" in shown
    assert "Type a question" not in shown
    assert started is None and claude.item_session(str(item)) is None  # the mirror stays clean
    assert claude.read_states() == {}


def test_names_and_keys():
    item = {"path": "/v/palace/work/024-real-claude.md", "cwd": "/v/palace"}
    note = {"path": "/v/palace/README.md", "cwd": "/v/palace"}
    assert claude.talk_key(item) == "/v/palace/work/024-real-claude.md" and claude.talk_key(note) == "/v/palace"
    assert claude.pane_name(claude.talk_key(item)) == "Claude on palace 024"
    assert claude.pane_name(claude.talk_key(note)) == "Claude on palace"
    spec = claude.pane_spec("/v/palace", "/v/palace")
    assert spec["name"] == "Claude on palace" and spec["cwd"] == "/v/palace"
    assert spec["argv"][3:] == ["_claude", "/v/palace", "/v/palace", str(state_home()), "no-claude-in-tests"]
    assert claude.pane_spec("/v/palace", "/v/palace", done=True)["argv"][7:] == ["--done", "no-claude-in-tests"]
    assert claude.pane_spec("/v/palace", "/v/palace", mirror="build-box")["argv"][7:] == [
        "--mirror", "build-box", "no-claude-in-tests"]


def test_cold_at_reads_the_last_reply_else_when_the_state_began(tmp_path):
    cwd = tmp_path / "vault" / "garden"
    entry = {"session": "s1", "cwd": str(cwd), "since": time.time() - 120}
    # No transcript: an hour after the state began.
    assert claude.cold_at(entry) == (entry["since"] + 3600, 3600)
    assert claude.cold_at({"session": "s1", "cwd": str(cwd)}) is None
    # The last reply, and its cache's life: 5 minutes in overage.
    reply(claude.transcript(str(cwd), "s1"), 60, 50_000, "5m")
    at, life = claude.cold_at(entry)
    assert life == 300 and abs(at - (time.time() - 60 + 300)) < 2
    # Read again once the transcript changes.
    time.sleep(0.01)
    reply(claude.transcript(str(cwd), "s1"), 60, 50_000, "1h")
    assert claude.cold_at(entry)[1] == 3600
