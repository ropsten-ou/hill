import asyncio
import inspect
import json
import time
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from rich.style import Style
from textual import events
from textual.worker import WorkerCancelled

import hill
import hill.app
import hill_client
from keyline import Keyline
from mdvault import Note
from hill import claude
from hill.app import APP_KEYS, COMMANDS, HELP, NotesScreen, NotesTree, PalaceApp, read_sync
from hill.config import PROFILE, README
from hill.restart import LOOP, RESTART, RESTART_ALL, CodeWatch, own_files
from hill.sync import done_states, git_state, last_commits, sync_vault


RUN_PROGRAM = PalaceApp.run_program
"""The real one, which the tests otherwise replace (env)."""


@pytest.fixture
def env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "projects").mkdir(parents=True)
    (vault / "Root.md").write_text("---\ntitle: Root\n---\nStart here.\n\n[[Projects]]\n")
    (vault / "Projects.md").write_text("# Local projects\n\n[[garden]]\n")
    (vault / "projects" / "garden.md").write_text("---\ntags: [project]\n---\n# garden\n\nListed in [[Projects]].\n")
    micro = tmp_path / "micro"
    micro.mkdir()
    monkeypatch.setenv("PALACE_VAULT", str(vault))
    monkeypatch.setenv("MICRO_CONFIG_HOME", str(micro))
    monkeypatch.setenv("PALACE_CONFIG_HOME", str(tmp_path / "palace"))
    # Never start real programs from the tests.
    launched, cwds = [], []

    def record(self, argv, cwd=None, then=None):
        launched.append(argv)
        cwds.append(cwd)
        if then is not None:
            then()  # the program has exited

    monkeypatch.setattr(PalaceApp, "run_program", record)
    return SimpleNamespace(root=tmp_path, launched=launched, cwds=cwds)


class FakeStrip:
    """Stands in for hill-ops's channel: records palace's hello and what it
    sends, and holds palace's handlers so a test can talk back."""

    def __init__(self):
        self.hello = None
        self.sent = []
        self.handlers = {}

    def from_env(self, app, profile, keys, commands=(), help=(), docs=None, tree=()):
        self.hello = (app, profile, keys)
        self.commands, self.help, self.docs, self.tree = commands, help, docs, tree
        return self

    def asked(self):
        return [params for method, params in self.sent if method == "ask"]

    def on(self, method, handler):
        self.handlers[method] = handler

    def send(self, method, **params):
        self.sent.append((method, params))

    async def run(self):
        pass  # the real one stays connected; drive() waits for palace's workers


@pytest.fixture
def scripts(env):
    """The folder has ~/projects's sync and scan scripts, which do nothing."""
    for name in ("sync", "scan"):
        script = env.root / "vault" / name
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)


@pytest.fixture
def strip(monkeypatch):
    """palace running inside hill-ops."""
    fake = FakeStrip()
    monkeypatch.setattr(hill.app, "Client", fake)
    return fake


def drive(*keys, size=(110, 36), typing=(), place=False):
    """Run palace, pressing keys; a callable among them is called with the
    app instead, or with the app and the pilot if it takes both (and
    awaited, if it's async). Each run starts on the first note, unless
    `place`: then where the run before left it."""
    if not place:
        (hill.app.state_home() / hill.app.PLACE).unlink(missing_ok=True)
    result = {}

    async def go():
        app = PalaceApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for key in keys:
                if callable(key):
                    done = key(app, pilot) if len(inspect.signature(key).parameters) == 2 else key(app)
                    if inspect.isawaitable(done):
                        await done
                elif key.startswith("type:"):
                    await pilot.press(*key[5:])
                else:
                    await pilot.press(key)
                await pilot.pause()
            await _workers_done(app)
            await pilot.pause()
            notes = app.get_screen_notes()
            tree = notes.query_one(NotesTree)
            result["titles"] = [
                str(node.label).split("  ")[0]
                for node in _walk(tree.root)
                if isinstance(node.data, Note)
            ]
            result["current"] = notes.current.title if notes.current else None
            result["header"] = str(notes.query_one("#header").render())
            result["preview_title"] = str(notes.query_one("#preview-title").render())
            result["preview"] = notes.query_one("#preview").source
            result["calendar_hidden"] = notes.query_one("#calendar-pane").has_class("-hidden")
            result["preview_hidden"] = notes.query_one("#preview-pane").has_class("-hidden")
            result["active_keys"] = [k.id for k in notes.query(Keyline) if k.has_class("-active")]
            result["list_keys"] = [k.label for k in notes.query_one("#list-keys", Keyline).keys]
            result["running"] = app.is_running
            result["return_code"] = app.return_code
            result["visible"] = [(d, label) for d, label in _visible(tree.root)]
            result["in_map"] = notes.in_map
            if notes.in_map:
                result["map"] = [(d, label) for d, label in _visible(notes.query_one("#map").root)]

    asyncio.run(go())
    return result


async def _workers_done(app):
    """Wait for palace's workers. One a newer exclusive one cancelled, such
    as a git poll on a short interval, is done too; one that failed fails."""
    for result in await asyncio.gather(*(worker.wait() for worker in app.workers), return_exceptions=True):
        if isinstance(result, BaseException) and not isinstance(result, WorkerCancelled):
            raise result


def sessions(states, **entry):
    """Claude's sessions as their hooks leave them, each `{key: state}`, then
    read as palace does every second."""
    def act(app):
        for key, state in states.items():
            claude._write_text(claude.state_file(key), json.dumps({"key": key, "state": state, "pane": "%9"} | entry))
        app.check_claude()
    return act


def _visible(node, depth=0):
    yield depth, str(node.label)
    if node.is_expanded:
        for child in node.children:
            yield from _visible(child, depth + 1)


def _walk(node):
    for child in node.children:
        yield child
        yield from _walk(child)


def test_lists_notes_with_folders_and_tags(env):
    result = drive()
    assert result["titles"] == ["garden", "Projects", "Root"]  # folders first
    assert "3 notes" in result["header"] and "not in git" in result["header"]


def test_work_items_are_marked_by_status_and_their_folders(env):
    vault = env.root / "vault"
    (vault / "projects" / "work").mkdir()
    for name, status in [("001-ask", "waiting"), ("002-done", "done"), ("003-go", "ready"),
                         ("004-job", "running"), ("005-plain", "open"), ("006-more", "waiting"),
                         ("007-gone", "dropped")]:
        (vault / "projects" / "work" / f"{name}.md").write_text(f"---\nstatus: {status}\n---\n")
    visible = drive()["visible"]
    counts = "  ● 1 open  ● 1 ready  ● 2 waiting  ● 1 running"  # not done or dropped ones
    assert (1, "projects/" + counts) in visible
    assert (2, "work/" + counts) in visible
    for line in ["001-ask  ● waiting", "002-done  ● done", "003-go  ● ready",
                 "004-job  ● running", "005-plain  ● open", "007-gone  \U0001F573️ dropped"]:
        assert (3, line) in visible
    # A closed folder still says what it holds.
    visible = drive("up", "enter")["visible"]
    assert (2, "work/" + counts) in visible
    assert not any("001-ask" in label for _, label in visible)


def test_only_work_items_get_marks(env, strip):
    vault = env.root / "vault"
    (vault / "projects" / "work").mkdir()
    (vault / "projects" / "work" / "001-ask.md").write_text("---\nstatus: waiting\n---\n")
    (vault / "projects" / "work" / "notes.md").write_text("---\nstatus: open\n---\n")
    decide = "## Before\n- [ ] Decide: this?\n"
    (vault / "projects" / "pattern.md").write_text(f"---\nstatus: open\n---\n{decide}")
    visible = drive()["visible"]
    assert (2, "pattern") in visible
    assert (3, "notes") in visible
    assert (3, "001-ask  ● waiting") in visible
    assert drive(command("status open"))["titles"] == []


def test_a_sample_shows_grey_and_counts_nowhere(env, strip):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    (work / "001-ask.md").write_text("---\nstatus: waiting\n---\n")
    (work / "002-beds-sample.md").write_text("---\nstatus: waiting\n---\n## Before\n- [ ] Decide: this?\n")
    (work / "003-old-sample.md").write_text("---\nstatus: done\n---\n")
    visible = drive()["visible"]
    assert (2, "work/  ● 1 waiting") in visible
    assert (3, "002-beds-sample  ● sample · waiting ❓1") in visible
    assert (3, "003-old-sample  ● sample · done") in visible
    assert drive(command("status waiting"))["titles"] == ["001-ask"]
    assert drive(command("status decide"))["titles"] == []
    from hill import app, claude
    assert claude.talk_key({"path": str(work / "002-beds-sample.md"), "cwd": str(work.parent)}) == str(work.parent)
    assert not app.WORK_ITEM.search("projects/work/002-beds-sample.md")


def test_the_scan_marks_due_items_and_dead_jobs(env, strip, work_scan, scripts):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    for name, status in [("001-ask", "waiting"), ("002-go", "ready"), ("003-job", "running"), ("004-later", "waiting")]:
        (work / f"{name}.md").write_text(f"---\nstatus: {status}\n---\n")
    work_scan.parent.mkdir()
    work_scan.write_text(json.dumps({"items": [
        {"path": "projects/work/001-ask.md", "due": True},
        {"path": "projects/work/003-job.md", "due": True, "job": {"state": "dead", "detail": "pid 7 gone"}},
        {"path": "projects/work/004-later.md", "due": False},
        {"path": "elsewhere/work/001-x.md", "due": True},  # not in this vault
    ]}))
    visible = drive()["visible"]
    counts = "  ● 1 ready  ● 2 waiting  ● 1 running  ⏳ 1 due  🤍 1 dead"
    assert (2, "work/" + counts) in visible
    for line in ["001-ask  ● waiting ⏳", "002-go  ● ready", "003-job  ● running 🤍", "004-later  ● waiting"]:
        assert (3, line) in visible
    assert drive(command("status dead"))["titles"] == ["003-job"]
    assert drive(command("status due"))["titles"] == ["001-ask"]


def test_open_decisions_show_after_the_mark(env, strip):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    before = "## Before\n- [ ] Decide: this?\n- [x] Decided 2026-10-05: that (Pierre)\n- [ ] Decide: or so?\n"
    (work / "001-ask.md").write_text(f"---\nstatus: waiting\n---\n{before}")
    (work / "002-one.md").write_text("---\nstatus: waiting\n---\n## Before\n- [ ] Decide: one?\n")
    (work / "003-none.md").write_text("---\nstatus: ready\n---\n## Before\n- [x] Decided 2026-10-05: yes\n")
    visible = drive()["visible"]
    assert (2, "work/  ● 1 ready  ● 2 waiting  ❓ 3 decide") in visible
    for line in ["001-ask  ● waiting ❓2", "002-one  ● waiting ❓1", "003-none  ● ready"]:
        assert (3, line) in visible
    assert drive(command("status decide"))["titles"] == ["001-ask", "002-one"]


def test_the_overview_lists_what_waits_on_you_and_a_pick_selects_it(env, strip, work_scan, scripts):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    items = [("001-ask", "waiting"), ("002-go", "ready"), ("003-job", "running"), ("004-busy", "doing"),
             ("005-later", "open"), ("006-talks", "doing")]
    for name, status in items:
        (work / f"{name}.md").write_text(f"---\nstatus: {status}\nnext: the next step\n---\n")
    work_scan.parent.mkdir()
    work_scan.write_text(json.dumps({"scanned": "2026-10-05T16:43:00Z", "host": "mac", "items": [
        {"path": "projects/work/003-job.md", "due": True, "job": {"state": "dead"}},
    ]}))
    seen = {}

    def pick(app):
        seen["overview"] = [p for m, p in strip.sent if m == "overview"][-1]
        strip.handlers["overview.pick"]({"id": "projects/work/002-go.md"})

    result = drive(sessions({str(work / "004-busy.md"): "idle", str(work / "006-talks.md"): "asks"}), pick)
    overview = seen["overview"]
    plain = ["".join(span[0] for span in line["text"]) for line in overview["lines"]]
    assert plain == [
        "  projects 006 006-talks ✦ asks",
        "● projects 003 003-job 🤍",
        "● projects 001 001-ask",
        "● projects 002 002-go",
        "  projects 004 004-busy ✦",  # doing has no mark; its session puts it here
    ]
    assert [line["id"] for line in overview["lines"]][:2] == ["projects/work/006-talks.md", "projects/work/003-job.md"]
    assert overview["lines"][2]["help"] == "Waits for your answer. Next: the next step"
    assert overview["title"] == "Overview" and overview["verb"] == "select"
    assert "last scan" in overview["where"] and overview["where"].endswith(" on mac")
    assert result["current"] == "002-go"
    # It's sent again only when it changed.
    assert len([m for m, _ in strip.sent if m == "overview"]) == 2  # before the sessions, and after


def test_the_overview_zooms_like_the_list_and_shows_sessions_on_projects(env, strip, monkeypatch):
    vault = env.root / "vault"
    for project in ("alpha", "beta", "gamma"):
        (vault / project / "work").mkdir(parents=True)
        (vault / project / "README.md").write_text(f"# {project}\n")
        (vault / project / "work" / "001-wait.md").write_text("---\nstatus: waiting\n---\n")
    instance = env.root / "instance"
    instance.mkdir()
    monkeypatch.setenv("HILL_INSTANCE", str(instance))
    monkeypatch.setenv("HILL_RUN", str(env.root / "run"))
    settings = env.root / "palace" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    seen = []

    def look(app):
        overview = [p for m, p in strip.sent if m == "overview"][-1]
        seen.append((overview["where"], [line if line.get("separator") else "".join(span[0] for span in line["text"])
                                         for line in overview["lines"]], [line.get("id") for line in overview["lines"]]))

    # A session on beta that idles, and one on gamma that asks.
    since = time.time() - 12 * 60 - 5
    talk = sessions({str(vault / "beta"): "idle", str(vault / "gamma"): "asks"}, since=since)
    for style in ("only", "top"):
        settings.write_text(json.dumps({"notes": {"zoom_on": 1, "zoom_style": style}}))
        drive(talk, look, lambda app: app.select_item("beta/README.md"), "z", look, "z", look)
    unzoomed, only, back, _, top, _ = seen
    assert unzoomed[1] == [
        "✦ gamma asks 12m",
        "● alpha 001 001-wait", "● beta 001 001-wait", "● gamma 001 001-wait",
        "✦ beta 12m",
    ]
    assert unzoomed[2][0] == "gamma/README.md" and unzoomed[2][-1] == "beta/README.md"
    assert only[1] == ["● beta 001 001-wait", "✦ beta 12m"] and "zoomed on 1" in only[0]
    assert back == unzoomed
    assert top[1] == ["● beta 001 001-wait", "✦ beta 12m", {"separator": True},
                      "✦ gamma asks 12m", "● alpha 001 001-wait", "● gamma 001 001-wait"]
    assert "zoomed" not in top[0]
    # The session ends, and its line goes.
    result = []
    drive(talk, lambda app: claude.state_file(str(vault / "gamma")).unlink(), lambda app: app.check_claude(),
          lambda app: result.append([p for m, p in strip.sent if m == "overview"][-1]))
    assert not any("gamma asks" in "".join(span[0] for span in line["text"]) for line in result[0]["lines"])


def test_the_overview_shows_what_sync_could_not_do_first(env, strip, work_scan, scripts):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    (env.root / "vault" / "projects" / "README.md").write_text("# projects\n")
    (work / "001-ask.md").write_text("---\nstatus: waiting\n---\n")
    work_scan.parent.mkdir()
    (work_scan.parent / "projects-sync.json").write_text(json.dumps({
        "synced": "2026-10-05T16:43Z", "host": "mac", "pull": True,
        "notes": [{"repo": "projects", "note": "2 uncommitted"}],
        "stuck": [{"repo": "projects", "note": "not pulled: a local change is in a file the pull changes"},
                  {"repo": "~/.claude/skills", "note": "fetch failed"}],
    }))
    seen = {}

    def pick(app):
        seen["overview"] = [p for m, p in strip.sent if m == "overview"][-1]
        strip.handlers["overview.pick"]({"id": seen["overview"]["lines"][0]["id"]})

    result = drive(pick)
    lines = seen["overview"]["lines"]
    plain = ["".join(span[0] for span in line["text"]) for line in lines]
    assert plain[:2] == [
        "⚠ projects not pulled: a local change is in a file the pull changes",
        "⚠ ~/.claude/skills fetch failed",
    ]
    assert plain[2].startswith("⚠ sync last ran 2026-10-05 ") and plain[2].endswith(" on mac")
    assert plain[3] == "● projects 001 001-ask"
    assert lines[0]["id"] == "projects/README.md" and "sync couldn't do this" in lines[0]["help"]
    assert "has it stopped?" in lines[2]["help"]
    assert result["current"] == "README"


def test_a_repo_sync_could_not_pull_is_marked_on_its_folder(env, strip, work_scan, scripts):
    (env.root / "vault" / "projects" / "README.md").write_text("# projects\n")
    state = work_scan.parent / "projects-sync.json"
    work_scan.parent.mkdir()
    state.write_text(json.dumps({"synced": "2026-10-05T16:43Z", "host": "mac", "stuck": [
        {"repo": "projects", "note": "not pulled: a local change is in a file the pull changes"},
        {"repo": "projects", "note": "3 to pull"},
        {"repo": "~/.claude/skills", "note": "fetch failed"},
    ]}))
    assert (1, "projects/  ⚠ not pulled, 3 to pull") in drive()["visible"]
    # Once sync pulls it, the mark goes.
    state.write_text(json.dumps({"synced": "2026-10-05T16:58Z", "host": "mac", "stuck": []}))
    assert (1, "projects/") in drive()["visible"]


def test_sync_is_late_after_an_hour(work_scan):
    work_scan.parent.mkdir()
    (work_scan.parent / "projects-sync.json").write_text(json.dumps(
        {"synced": "2026-10-05T16:43Z", "host": "mac", "stuck": []}))
    then = datetime(2026, 10, 5, 17, 30, tzinfo=timezone.utc)
    assert read_sync(then)["stuck"] == [] and read_sync(then)["late"] is None
    assert read_sync(then + timedelta(hours=1))["late"].endswith(" on mac")
    (work_scan.parent / "projects-sync.json").unlink()
    assert read_sync() == {}


def test_w_shows_only_work_in_progress(env, strip):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    for name, status in [("001-ask", "waiting"), ("002-done", "done"), ("003-go", "ready"), ("004-gone", "dropped")]:
        (work / f"{name}.md").write_text(f"---\nstatus: {status}\n---\n")
    result = drive("w")
    assert result["titles"] == ["001-ask", "003-go"]
    assert "work in progress" in result["header"] and "all notes" in result["list_keys"]
    assert len(drive("w", "w")["titles"]) == 7 and len(drive("w", "escape")["titles"]) == 7
    assert drive(command("status dropped"))["titles"] == ["004-gone"]
    assert drive(command("status ready"))["titles"] == ["003-go"]
    assert len(drive(command("status nonsense"))["titles"]) == 7  # all notes, as before


def test_done_items_hide_after_a_while(env):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    today, old = date.today(), date.today() - timedelta(days=10)
    yesterday = today - timedelta(days=1)
    for name, status, since in [("001-old", "done", old), ("002-new", "done", today),
                                ("003-gone", "dropped", old), ("004-open", "open", old),
                                ("005-undated", "done", None), ("006-yesterday", "done", yesterday)]:
        dated = f"since: {since}\n" if since else ""
        (work / f"{name}.md").write_text(f"---\nstatus: {status}\n{dated}---\n")
    prefs = env.root / "palace" / "settings.json"
    prefs.parent.mkdir()
    shown = {}
    for hide in ["never", "now", "day", "week", "month"]:
        prefs.write_text(json.dumps({"notes": {"hide_done": hide}}))
        shown[hide] = [t for t in drive()["titles"] if t[0] == "0"]
    assert shown == {
        "never": ["001-old", "002-new", "003-gone", "004-open", "005-undated", "006-yesterday"],
        "now": ["004-open"],
        "day": ["002-new", "004-open", "005-undated"],  # until tomorrow, not for 24 hours
        "week": ["002-new", "004-open", "005-undated", "006-yesterday"],  # not in git: its last change counts
        "month": ["001-old", "002-new", "003-gone", "004-open", "005-undated", "006-yesterday"],
    }
    # A search shows them again.
    prefs.write_text(json.dumps({"notes": {"hide_done": "now"}}))
    assert drive("slash", "type:old")["titles"] == ["001-old"]


def test_the_map_marks_work_items(env):
    projects = env.root / "vault" / "projects"
    (projects / "garden.md").unlink()
    (projects / "work").mkdir()
    (projects / "work" / "001-garden.md").write_text(
        "---\ntitle: garden\nstatus: waiting\n---\n# garden\n\nListed in [[Projects]].\n")
    labels = [label for _, label in drive("m")["map"]]
    assert any(label.startswith("garden  ● waiting") for label in labels)
    assert drive("g")["map"][0][1].startswith("garden  ● waiting")  # from the first note, garden


async def _outside(app):
    """Give palace's poll time to notice a change made outside it."""
    await asyncio.sleep(0.3)


def test_list_follows_changes_made_outside_palace(env, monkeypatch):
    monkeypatch.setattr(hill.app, "POLL_SECONDS", 0.05)
    vault = env.root / "vault"

    def add(app):
        (vault / "ideas").mkdir()
        (vault / "ideas" / "New.md").write_text("new")
        (vault / "Projects.md").unlink()

    # The cursor stays on garden, though New is now the first note.
    result = drive(add, _outside)
    assert result["titles"] == ["New", "garden", "Root"]
    assert (1, "ideas/") in result["visible"]
    assert result["current"] == "garden"

    def status(app):
        (vault / "projects" / "work").mkdir()
        (vault / "projects" / "work" / "001-go.md").write_text("---\nstatus: waiting\n---\n")

    # The cursor stays on a folder, and a status shows when it's set.
    result = drive("up", status, _outside)
    assert (1, "projects/  ● 1 waiting") in result["visible"]
    assert result["current"] is None  # on projects/

    def touch(app):
        (vault / "Other.md").write_text("other")

    # In the map, it stays open.
    result = drive("m", touch, _outside)
    assert result["in_map"] and "Other" in result["titles"]


def test_preview_shows_body_and_backlinks(env):
    result = drive("down", "down")  # garden -> Projects -> Root
    assert result["current"] == "Root"
    assert result["preview"].startswith("Start here.")
    assert "---" not in result["preview"]
    assert "linked from" not in result["preview_title"]
    projects = drive("down")
    assert "linked from garden, Root" in projects["preview_title"]


def test_search_filters_titles_and_esc_clears(env):
    result = drive("slash", "type:ro", "enter")
    assert result["titles"] == ["Projects", "Root"]
    assert result["list_keys"][0] == "all notes"
    assert drive("slash", "type:ro", "enter", "escape")["titles"] == ["garden", "Projects", "Root"]


def test_calendar_day_filters_notes(env):
    # Tab to the calendar, Enter picks today, when all three notes changed.
    result = drive("tab", "enter")
    assert result["titles"] == ["garden", "Projects", "Root"]
    assert "changed on" in result["header"]
    assert result["active_keys"] == ["calendar-keys"]
    result = drive("tab", "left", "enter")  # a week ago: nothing
    assert result["titles"] == []


def test_ret_and_a_click_open_folders_and_never_edit(env):
    # Ret on a note does nothing; on projects/ it closes it, and (closed
    # next time, so on Projects, under it) opens it again.
    result = drive("enter", "up", "enter")
    assert env.launched == []
    assert (1, "projects/") in result["visible"] and not any("garden" in l for _, l in result["visible"])
    assert any("garden" in label for _, label in drive("up", "enter")["visible"])

    async def click(app, pilot, line):
        await pilot.click("#notes", offset=(4, line))
        await pilot.pause()

    # A click on a note selects it; one on projects/, the first line, closes it.
    result = drive(lambda app, pilot: click(app, pilot, 2), lambda app, pilot: click(app, pilot, 0))
    assert env.launched == []
    assert result["current"] is None and not any("garden" in l for _, l in result["visible"])
    # In the map, Ret opens and closes a note's links, and doesn't edit.
    closed = drive("m", "up", "enter")["map"]
    opened = drive("m", "up", "enter", "enter")["map"]
    assert env.launched == [] and len(opened) > len(closed)


def test_the_list_marks_live_sessions_and_wraps_a_pushed_items(env, strip):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    doing, done, asks = work / "001-doing.md", work / "002-done.md", work / "003-asks.md"
    doing.write_text("---\nstatus: doing\n---\n")
    done.write_text("---\nstatus: done\n---\n")  # not in git: done and pushed, as far as palace can tell
    asks.write_text("---\nstatus: doing\n---\n")
    visible = drive(sessions({str(doing): "answering", str(done): "idle", str(asks): "asks"}))["visible"]
    assert (3, "001-doing ✦") in visible and (3, "003-asks ✦ asks") in visible
    # The idle one, done and pushed, is wrapped: its program stops, and its mark goes.
    assert [p for m, p in strip.sent if m == "panes.end"] == [{"names": ["Claude on projects 002"]}]
    assert (3, "002-done  ● done") in visible and not claude.state_file(str(done)).exists()


def test_a_session_parks_once_its_item_hands_off(env, strip):
    vault = repo_with_remote(env)
    work = vault / "projects" / "work"
    work.mkdir()
    items = {name: work / f"{n:03}-{name}.md" for n, name in enumerate(
        ["handed", "came-ready", "waiting", "waiting-new", "unwritten", "doing"], 1)}
    for name, path in items.items():
        path.write_text("---\nstatus: doing\n---\n" if name in ("handed", "doing")
                        else "---\nstatus: ready\n---\n" if name == "came-ready" else "---\nstatus: waiting\n---\n")
    git(vault, "add", "-A")
    git(vault, "commit", "-q", "-m", "items")
    items["unwritten"].write_text("---\nstatus: waiting\n---\nmore\n")  # not committed
    old, new = time.time() - 51 * 60, time.time() - 10 * 60
    idle = lambda *names, since: sessions({str(items[n]): "idle" for n in names}, since=since)

    def hand_off(app):
        items["handed"].write_text("---\nstatus: ready\n---\n")  # the session writes it back, and commits
        git(vault, "commit", "-q", "-m", "handed off", "--", str(items["handed"]))
        app.claude = (str(items["handed"]), str(vault / "projects"))  # its note is the first, so shown
        app.get_screen_notes().reload(quiet=True)
        app.get_screen_notes().wrap_done()

    # The next reading of the sessions doesn't end it again (it once did,
    # now and then, when it came before the end of the run).
    drive(idle("handed", "came-ready", "doing", "waiting-new", since=new),
          idle("waiting", "unwritten", since=old), hand_off, lambda app: app.check_claude())
    ended = [name for m, p in strip.sent if m == "panes.end" for name in p["names"]]
    # It turned ready while the session ran, and is committed: parked at once.
    # Waiting, idle 51 minutes and committed: parked. The rest stay: one
    # started on an item already ready (you came to talk), one waiting but
    # idle 10 minutes, one not written back, one doing.
    assert sorted(ended) == ["Claude on projects 001", "Claude on projects 003"]
    assert not claude.state_file(str(items["handed"])).exists()
    assert claude.state_file(str(items["came-ready"])).exists()


def test_a_session_on_a_project_parks_after_its_setting(env, strip):
    projects = env.root / "vault" / "projects"
    prefs = env.root / "palace" / "settings.json"
    prefs.parent.mkdir()
    ended = {}
    for after, since in [(50, 51), (50, 10), (0, 600)]:
        prefs.write_text(json.dumps({"claude": {"park_after": after}}))
        strip.sent.clear()
        drive(sessions({str(projects): "idle"}, since=time.time() - since * 60))
        ended[after, since] = [p for m, p in strip.sent if m == "panes.end"]
        claude.forget(str(projects))
    assert ended == {(50, 51): [{"names": ["Claude on projects"]}], (50, 10): [], (0, 600): []}


def test_a_session_parked_while_shown_is_offered_again(env, strip):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    item = work / "001-waits.md"
    item.write_text("---\nstatus: waiting\n---\n")  # not in git: written back, as far as palace can tell
    key, cwd = str(item), str(work.parent)
    seen = []

    def shown(app):
        app.claude = (key, cwd)

    drive(shown, sessions({key: "idle"}, since=time.time() - 51 * 60), lambda app: seen.append(app.claude))
    assert seen == [(key, cwd)]  # Claude's pane shows its program again, to carry it on
    assert [m for m, _ in strip.sent if m in ("panes.end", "panes")][-2:] == ["panes.end", "panes"]


def test_a_session_on_a_project_marks_its_readme_else_its_folder_with_a_timer(env, strip):
    projects = env.root / "vault" / "projects"
    (projects / "README.md").write_text("Where they are.\n")
    key = str(projects)
    seen = {}

    def look(name):
        def act(app):
            tree = app.get_screen_notes().query_one("#notes", NotesTree)
            seen[name] = {str(node.label).split("  ")[0]: tree.render_label(node, Style(), Style()).plain
                          for node in _walk(tree.root)}
        return act

    def close_projects(app):
        tree = app.get_screen_notes().query_one("#notes", NotesTree)
        next(node for node in _walk(tree.root) if str(node.label).startswith("projects/")).collapse()

    drive(sessions({key: "idle"}, since=time.time() - 125), look("idle"),
          sessions({key: "answering"}), look("answering"), close_projects, look("closed"))
    # Idle: the README has the white ✦, and its line the timer at its right edge.
    readme = seen["idle"]["README ✦"]
    assert readme.endswith(" 2m") and readme.rstrip("2m").endswith("  ")
    assert not seen["idle"]["projects/"].endswith("✦")  # open, with its README: the README has it
    # Answering: no timer.
    assert seen["answering"]["README ✦"].rstrip().endswith("✦")
    # Closed, the folder shows it, so it stays in sight.
    assert seen["closed"]["projects/"].rstrip().endswith("✦")
    # With no README, the folder line has it, open too; it goes with the session.
    (projects / "README.md").unlink()
    def open_projects(app):
        tree = app.get_screen_notes().query_one("#notes", NotesTree)
        next(node for node in _walk(tree.root) if str(node.label).startswith("projects/")).expand()

    drive(open_projects, sessions({key: "asks"}, since=time.time() - 30), look("asks"))
    folder = seen["asks"]["projects/"]
    assert folder.startswith("▼ projects/ ✦ asks  ") and folder.endswith(" 30s")
    claude.forget(key)
    drive(lambda app: app.check_claude(), look("gone"))
    assert "✦" not in seen["gone"]["projects/"]


def test_the_cursor_leaves_a_marks_colour_alone(env):
    projects = env.root / "vault" / "projects"
    (projects / "garden.md").unlink()
    (projects / "work").mkdir()
    (projects / "work" / "001-garden.md").write_text("---\ntitle: garden\nstatus: waiting\n---\n# garden\n")
    seen = {}

    def look(app):
        tree = app.get_screen_notes().query_one("#notes", NotesTree)
        node = tree.cursor_node  # garden, the first note
        cursor = Style(color="#000001", bgcolor="#000002")
        text = tree.render_label(node, Style(), cursor)
        title, mark = text.plain.index("garden"), text.plain.index("●")
        seen["title"] = text.get_style_at_offset(app.console, title)
        seen["mark"] = text.get_style_at_offset(app.console, mark)

    drive(look)
    assert seen["title"].color.name == "#000001" and seen["title"].bgcolor.name == "#000002"
    assert seen["mark"].color.name != "#000001" and seen["mark"].bgcolor.name == "#000002"


def test_palace_starts_where_it_was_left(env):
    # On Root, then a restart: still on Root, with its preview.
    drive("down", "down")
    result = drive(place=True)
    assert result["current"] == "Root" and result["preview"].startswith("Start here.")
    # On projects/, a folder.
    drive("up", "up", "up", place=True)
    assert drive(place=True)["current"] is None  # on projects/, not garden
    # In the map, on Projects.
    drive("m", "down", place=True)
    result = drive(place=True)
    assert result["in_map"] and result["current"] == "Projects"
    # Back in the list, on Projects; gone since: the first note.
    assert drive("m", place=True)["current"] == "Projects"
    (env.root / "vault" / "Projects.md").unlink()
    result = drive(place=True)
    assert not result["in_map"] and result["current"] == "garden"


def test_each_of_hills_instances_keeps_its_own_place_and_view(env, monkeypatch):
    one, two = env.root / "one", env.root / "two"
    one.mkdir()
    two.mkdir()

    def hide_calendar(app):
        app.prefs.set(("notes", "calendar"), False, True)

    monkeypatch.setenv("HILL_INSTANCE", str(one))
    drive("down", "down", hide_calendar)  # Root
    monkeypatch.setenv("HILL_INSTANCE", str(two))
    drive("up", "up", "up", place=True)  # a new instance starts where the last one was: Root, then up to projects/
    assert drive(place=True)["current"] is None
    monkeypatch.setenv("HILL_INSTANCE", str(one))
    assert drive(place=True)["current"] == "Root"  # each where it was left
    assert json.loads((one / "palace.json").read_text()) == {"notes": {"calendar": False, "preview": True}}
    assert json.loads((two / "palace.json").read_text())["notes"]["calendar"] is False  # taken from the last change, once
    # Each is labelled by the projects it was last in.
    assert json.loads((two / "label.json").read_text()) == ["projects"]


def test_palace_labels_its_instance_by_its_last_projects(tmp_path):
    root = tmp_path
    for name in ("a", "b"):
        (root / name).mkdir()
    assert hill.app.project_of("a/work/001.md", root) == "a"
    assert hill.app.project_of("b", root) == "b"  # the folder itself
    assert hill.app.project_of("Home.md", root) is None
    assert hill.app.project_of(None, root) is None


def test_projects_that_count_as_one():
    names = ["lantern-3", "kite-2026", "kite-2026-tails", "atlas", "atlas-overlay",
             "fern-4", "zinc-desk", "zinc-mcp", "quill-mcp"]
    one = hill.app.one_project
    assert one("zinc-mcp", "zinc-desk") and one("atlas", "atlas-overlay")
    assert one("kite-2026", "kite-2026-tails")
    for alone in ("lantern-3", "fern-4", "quill-mcp"):
        assert [n for n in names if one(n, alone)] == [alone]
    recent = ["zinc-desk", "atlas-overlay", "zinc-mcp", "gone", "fern-4", "quill-mcp"]
    assert hill.app.zoom_groups(recent, names, 3) == [
        ["zinc-desk", "zinc-mcp"], ["atlas-overlay", "atlas"], ["fern-4"]]


def test_z_zooms_on_the_last_projects_in_either_style(env, monkeypatch):
    vault = env.root / "vault"
    for project in ("alpha", "beta", "zinc-desk", "zinc-mcp"):
        (vault / project).mkdir()
        (vault / project / f"{project}.md").write_text(f"# {project}\n")
    # This hill-ops instance worked on zinc-mcp, then alpha, then beta;
    # zoomed on 2: the two zinc projects take one.
    run = env.root / "run"
    run.mkdir()
    monkeypatch.setenv("HILL_RUN", str(run))
    instance = env.root / "instance"
    instance.mkdir()
    monkeypatch.setenv("HILL_INSTANCE", str(instance))
    settings = env.root / "palace" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"notes": {"zoom_on": 2}}))
    recent = ["zinc-mcp", "alpha", "beta"]

    def top_level(result):
        return [label.split("  ")[0] for depth, label in result["visible"] if depth == 1]

    def start(select="Root.md"):
        hill.app.state_home().mkdir(parents=True, exist_ok=True)
        for path in hill.app._place_files():
            path.write_text(json.dumps({"select": select, "map": None}))
        (instance / "palace-session.json").write_text(json.dumps({"recent": recent}))

    everything = ["alpha/", "beta/", "projects/", "zinc-desk/", "zinc-mcp/", "Projects", "Root"]
    # Starting on Root, no project counts as worked on, not even the first
    # line, where the cursor is before it moves to Root.
    start()
    drive(place=True)
    assert json.loads((instance / "palace-session.json").read_text())["recent"] == recent
    start()
    result = drive("z", place=True)
    assert top_level(result) == ["alpha/", "zinc-desk/", "zinc-mcp/", "Projects", "Root"]
    assert "zoomed on 2" in result["header"] and result["current"] == "Root"
    assert top_level(drive(place=True)) == top_level(result)  # still zoomed next time
    result = drive("z", place=True)
    assert top_level(result) == everything and "zoomed" not in result["header"]
    assert result["current"] == "Root"
    # A search looks in the whole folder.
    start()
    assert "beta" in drive("z", "slash", "type:beta", place=True)["titles"]
    # At the top: those, most recent first, a line, then the rest.
    settings.write_text(json.dumps({"notes": {"zoom_on": 2, "zoom_style": "top"}}))
    start()
    result = drive("z", place=True)
    assert top_level(result) == ["zinc-mcp/", "zinc-desk/", "alpha/", hill.app.ZOOM_SEPARATOR,
                                 "beta/", "projects/", "Projects", "Root"]
    assert "zoomed" not in result["header"]
    assert top_level(drive("z", place=True)) == everything
    # A new hill-ops instance starts where the last one was, but has worked
    # on nothing yet: z has nothing to zoom on, not even where it starts,
    # until a note is selected in a project.
    other = env.root / "other-instance"
    other.mkdir()
    monkeypatch.setenv("HILL_INSTANCE", str(other))
    monkeypatch.setenv("HILL_RUN", str(env.root / "other-run"))
    # The other instance's projects stay in its own folder.
    (hill.app.state_home() / hill.app.PLACE).write_text(json.dumps({"select": "beta/beta.md", "map": None}))
    assert top_level(drive("z", place=True)) == everything
    result = drive(lambda app: app.select_item("alpha/alpha.md"), "z", place=True)
    assert top_level(result) == ["alpha/", hill.app.ZOOM_SEPARATOR, "beta/", "projects/", "zinc-desk/",
                                 "zinc-mcp/", "Projects", "Root"]
    assert json.loads((other / "palace-session.json").read_text())["recent"] == ["alpha"]


def test_the_zoom_line_goes_across_the_whole_list(env, monkeypatch):
    vault = env.root / "vault"
    for project in ("alpha", "beta"):
        (vault / project).mkdir()
        (vault / project / f"{project}.md").write_text(f"# {project}\n")
    instance = env.root / "instance"
    instance.mkdir()
    monkeypatch.setenv("HILL_INSTANCE", str(instance))
    monkeypatch.setenv("HILL_RUN", str(env.root / "run"))
    settings = env.root / "palace" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"notes": {"zoom_on": 1, "zoom_style": "top"}}))
    seen = {}

    def look(app):
        tree = app.get_screen_notes().query_one("#notes", NotesTree)
        node = next(node for node in _walk(tree.root) if str(node.label) == hill.app.ZOOM_SEPARATOR)
        line = tree.render_label(node, Style(), Style()).plain
        seen[app.size.width] = (line, tree.scrollable_content_region.width - tree._guide_width(node),
                                tree.get_label_width(node))

    async def widen(app, pilot):
        await pilot.resize_terminal(140, 30)

    # One run, so that the list is redrawn when it gets wider.
    drive(lambda app: app.select_item("alpha/alpha.md"), "z", look, widen, look, size=(80, 30))
    (narrow, room, label), (wide, room2, label2) = seen.values()
    assert set(narrow) == set(wide) == {"─"} and len(narrow) == room and len(wide) == room2 > room
    # It takes no more room than its label, so the list never scrolls sideways for it.
    assert label == label2 == len(hill.app.ZOOM_SEPARATOR)


def test_the_zoom_lasts_as_long_as_the_instance(env, monkeypatch):
    vault = env.root / "vault"
    for project in ("alpha", "beta", "gamma"):
        (vault / project).mkdir()
        (vault / project / f"{project}.md").write_text(f"# {project}\n")
    settings = env.root / "palace" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"notes": {"zoom_on": 1}}))

    def top_level(result):
        return [label.split("  ")[0] for depth, label in result["visible"] if depth == 1]

    def hill(instance, run):
        """A hill-ops on `instance`, started again in a new session folder."""
        (env.root / instance).mkdir(exist_ok=True)
        (env.root / run).mkdir(exist_ok=True)
        monkeypatch.setenv("HILL_INSTANCE", str(env.root / instance))
        monkeypatch.setenv("HILL_RUN", str(env.root / run))

    hill("instance", "run-1")
    result = drive(lambda app: app.select_item("beta/beta.md"), "z", place=True)
    assert "zoomed on 1" in result["header"]
    # hill-ops ends (a quit, :restart all, a reboot) and is resumed: its
    # session folder is new, its instance the same, and so is the zoom.
    hill("instance", "run-2")
    result = drive(place=True)
    assert "zoomed on 1" in result["header"] and top_level(result) == ["beta/", "Projects", "Root"]
    # Another instance never sees it.
    hill("other", "run-3")
    assert "zoomed" not in drive(place=True)["header"]
    # A hill-ops that kept it in its session folder, before 2026-10-08,
    # keeps it too, until the instance has its own.
    (env.root / "run-4").mkdir()
    (env.root / "run-4" / "palace-session.json").write_text(json.dumps({"recent": ["gamma"], "zoom": [["gamma"]]}))
    hill("old", "run-4")
    result = drive(place=True)
    assert "zoomed on 1" in result["header"] and "gamma/" in top_level(result)


def test_text_selected_in_the_preview_goes_to_the_clipboard(env):
    copied = []

    async def select(app, pilot):
        await pilot.mouse_down("#preview", offset=(0, 0))
        await pilot.hover("#preview", offset=(9, 0))
        await pilot.mouse_up("#preview", offset=(9, 0))
        await pilot.pause()
        copied.append(app._clipboard)

    drive("down", "down", select)  # Root: "Start here."
    assert copied[0].startswith("Start h") and "Start here.".startswith(copied[0])


def test_e_opens_the_note_in_micro(env):
    drive("down", "down", "e")
    assert env.launched == [["micro", str(env.root / "vault" / "Root.md")]]


def test_new_note_is_created_and_opened(env):
    result = drive("down", "n", "type:Ideas", "enter")  # from a note at the top level
    assert (env.root / "vault" / "Ideas.md").exists()
    assert result["current"] == "Ideas"
    assert env.launched[-1] == ["micro", str(env.root / "vault" / "Ideas.md")]


def test_rename_renames_the_file(env):
    keys = ["down", "down", "r"] + ["backspace"] * 4 + ["type:Home", "enter"]
    result = drive(*keys)
    assert (env.root / "vault" / "Home.md").exists() and not (env.root / "vault" / "Root.md").exists()
    assert result["current"] == "Home"


def test_toggles_hide_panes_and_are_remembered(env):
    result = drive("c", "p")
    assert result["calendar_hidden"] and result["preview_hidden"]
    prefs = json.loads((env.root / "palace" / "settings.json").read_text())
    assert prefs == {"notes": {"calendar": False, "preview": False}}
    assert drive()["calendar_hidden"]


def test_inside_hill_palace_says_hello_with_its_profile_and_app_keys(env, strip, scripts):
    result = drive()
    assert strip.hello == ("palace", str(PROFILE), APP_KEYS)
    assert (strip.commands, strip.help) == (COMMANDS, HELP)
    # Keys for the whole app are in hill-ops's strip, not under the panes.
    assert result["list_keys"] == ["edit", "new", "search", "map", "in progress", "zoom", "rename"]


def test_outside_hill_the_pane_key_lines_keep_every_key(env):
    assert drive()["list_keys"] == ["edit", "new", "search", "map", "settings", "quit", "in progress", "zoom", "rename",
                                    "next pane"]


def git(repo, *args):
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr  # git says why


def test_sync_commits_changes(env):
    vault = env.root / "vault"
    git(vault, "init", "-q")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start")
    assert "3 changes not committed" in git_state(vault)
    subprocess.run(["git", "config", "user.name", "t"], cwd=vault)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=vault)
    assert sync_vault(vault) == "palace: committed 3 changes"
    assert "no remote" in git_state(vault)
    assert sync_vault(vault) == ""


def test_map_starts_at_the_home_note(env):
    result = drive("m")
    assert result["current"] == "garden"  # keeps the list's note
    assert result["in_map"]
    assert result["map"] == [(0, "Root"), (1, "Projects  1 link"), (2, "garden  1 link")]
    assert "map from Root" in result["header"]
    assert result["list_keys"][:3] == ["edit", "open/close", "map from here"]


def test_the_home_note_nearest_the_top_wins(env):
    vault = env.root / "vault"
    (vault / "Root.md").rename(vault / "Home.md")
    (vault / "Home.md").write_text("---\ntitle: Home\n---\n[[Projects]]\n")
    (vault / "projects" / "Root.md").write_text("---\ntitle: Root\n---\nAn old vault's home.\n")
    result = drive("m")
    assert result["map"][0] == (0, "Home")


def test_map_marks_cycles(env):
    # garden links back to Projects, which is already on the path.
    result = drive("m", "right")  # on garden, which links back to Projects
    assert (3, "↺ Projects") in result["map"]


def test_missing_link_can_be_created(env):
    root = env.root / "vault" / "Root.md"
    root.write_text(root.read_text() + "Later: [[Someday]]\n")
    # The map opens on garden; the missing note is the line below.
    result = drive("m", "down")
    assert result["map"][-1] == (1, "Someday  no note yet")
    assert result["list_keys"][0] == "create note"
    drive("m", "down", "e")
    assert (env.root / "vault" / "Someday.md").exists()
    assert env.launched[-1] == ["micro", str(env.root / "vault" / "Someday.md")]


def test_map_from_here_shows_who_links_to_it(env):
    # The map opens on the list's note (garden); up is Projects.
    result = drive("m", "up", "g")
    assert result["map"][0] == (0, "Projects")
    assert result["map"][1] == (1, "↖ linked from 2 notes")
    assert "map from Projects" in result["header"]


def test_notes_nothing_links_to_are_listed(env):
    (env.root / "vault" / "Loose.md").write_text("on its own")
    assert drive("m")["map"][-1] == (1, "not linked from anywhere (1)")


def test_preview_lists_links(env):
    assert "links to Projects" in drive("down", "down")["preview_title"]  # Root


def test_back_to_the_list_keeps_the_note(env):
    result = drive("m", "up", "m")
    assert not result["in_map"] and result["current"] == "Projects"


def test_micro_runs_in_the_notes_repository(env):
    project = env.root / "vault" / "projects"
    git(project, "init", "-q")
    drive("e")  # the first note, garden, is in projects/
    assert env.cwds[-1] == project
    drive("down", "down", "e")  # Root, at the top: the vault itself
    assert env.cwds[-1] == env.root / "vault"


def test_header_shows_the_git_state_of_the_notes_repository(env):
    project = env.root / "vault" / "projects"
    git(project, "init", "-q")
    assert "projects: 1 change not committed" in drive()["header"]  # garden, in projects/
    header = drive("down", "down")["header"]  # Root, at the top: no repository
    assert "not in git" in header and "projects:" not in header


def test_no_sync_for_a_folder_inside_a_repository(env):
    repo = env.root / "vault"
    git(repo, "init", "-q")
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo)
    assert sync_vault(repo / "projects") == ""
    assert "3 changes not committed" in git_state(repo)


def test_sync_commits_only_notes_and_documents(env):
    vault = env.root / "vault"
    git(vault, "init", "-q")
    subprocess.run(["git", "config", "user.name", "t"], cwd=vault)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=vault)
    (vault / "tool.py").write_text("print('code')")
    (vault / "photo.png").write_bytes(b"png")
    (vault / "map.canvas").write_text("{}")
    (vault / "api.schema.json").write_text("{}")
    (vault / "staged.py").write_text("x = 1")
    git(vault, "add", "staged.py")  # staged by hand: stays out of palace's commit
    assert "5 changes not committed" in git_state(vault)  # 3 notes, canvas, schema
    assert sync_vault(vault) == "palace: committed 5 changes"
    committed = subprocess.run(["git", "show", "--name-only", "--format="], cwd=vault, capture_output=True, text=True).stdout.split()
    assert sorted(committed) == ["Projects.md", "Root.md", "api.schema.json", "map.canvas", "projects/garden.md"]
    status = subprocess.run(["git", "status", "--porcelain"], cwd=vault, capture_output=True, text=True).stdout
    assert "A  staged.py" in status and "?? tool.py" in status and "?? photo.png" in status


def repo_with_remote(env):
    """The vault as a repository whose branch tracks a remote."""
    vault = env.root / "vault"
    git(vault, "init", "-q")
    git(vault, "config", "user.name", "t")
    git(vault, "config", "user.email", "t@t")
    git(vault, "commit", "-q", "--allow-empty", "-m", "start")
    git(env.root, "init", "-q", "--bare", "remote.git")
    git(vault, "remote", "add", "origin", str(env.root / "remote.git"))
    git(vault, "push", "-q", "-u", "origin", "HEAD")
    return vault


def test_a_done_item_shows_how_far_it_has_got_in_git(env, monkeypatch):
    vault = repo_with_remote(env)
    item = vault / "work" / "001-thing.md"
    item.parent.mkdir()
    item.write_text("---\nstatus: done\n---\n")
    outside = env.root / "elsewhere.md"
    outside.write_text("---\nstatus: done\n---\n")
    seen = [done_states([item, outside])]
    git(vault, "add", "work")
    git(vault, "commit", "-q", "-m", "item")
    seen.append(done_states([item])[item])
    git(vault, "push", "-q")
    seen.append(done_states([item])[item])
    assert seen == [{item: "to commit", outside: "done"}, "to push", "done"]
    # The day of its last commit, for when it has no since:.
    assert last_commits([item, outside]) == {item: date.today()}
    # The list marks it, and counts it in its folder while it wants a push.
    item.write_text("---\nstatus: done\n---\nmore\n")
    git(vault, "commit", "-q", "-am", "more")
    visible = drive()["visible"]
    assert (1, "work/  ● 1 to push") in visible and (2, "001-thing  ● to push") in visible
    # A push changes no note: palace's look at git shows it.
    monkeypatch.setattr(hill.app, "GIT_POLL_SECONDS", 0.05)
    visible = drive(lambda app: git(vault, "push", "-q"), _outside)["visible"]
    assert (1, "work/") in visible and (2, "001-thing  ● done") in visible


def test_sync_pushes_palace_commits(env):
    vault = repo_with_remote(env)
    assert sync_vault(vault) == "palace: committed 3 changes · pushed"
    assert "all pushed" in git_state(vault)


def test_sync_leaves_your_own_commits_to_you(env):
    vault = repo_with_remote(env)
    (vault / "tool.py").write_text("print('code')")
    git(vault, "add", "tool.py")
    git(vault, "commit", "-q", "-m", "Add a tool")
    # Pushing palace's commit would take yours along.
    assert sync_vault(vault) == "palace: committed 3 changes · not pushed, with 1 commit of your own waiting"
    assert "2 commits not pushed" in git_state(vault)
    # Once you've pushed yours, palace pushes its own again.
    git(vault, "push", "-q")
    (vault / "Root.md").write_text("Changed.\n")
    assert sync_vault(vault) == "palace: committed 1 change · pushed"


def test_sync_without_commits_of_its_own_pushes_nothing(env):
    vault = repo_with_remote(env)
    git(vault, "add", "Root.md")
    git(vault, "commit", "-q", "-m", "Edit Root by hand")
    assert sync_vault(vault, commit=False) == ""
    assert "1 commit not pushed" in git_state(vault)


def test_a_failed_push_is_reported_without_gits_message(env):
    vault = repo_with_remote(env)
    shutil.rmtree(env.root / "remote.git")  # the remote has gone
    failed = []
    report = sync_vault(vault, failed=failed.append)
    assert report.startswith("palace: committed 3 changes · push failed: ")  # git's message is for you
    assert failed == ["push failed"]  # hill-ops's log gets palace's words


def test_after_quitting_palace_says_hello_again_to_report_the_sync(monkeypatch):
    folder = Path(tempfile.mkdtemp(prefix="pw", dir="/tmp"))  # socket paths must be short
    received = []

    async def go():
        async def serve(reader, writer):
            while raw := await reader.readline():
                received.append(json.loads(raw))

        server = await asyncio.start_unix_server(serve, path=str(folder / "c.sock"))
        monkeypatch.setenv("HILL_SOCKET", str(folder / "c.sock"))
        await asyncio.to_thread(hill._report_to_hill, ["push failed"])
        await asyncio.sleep(0.1)
        server.close()

    try:
        asyncio.run(go())
        monkeypatch.setenv("HILL_SOCKET", str(folder / "gone.sock"))
        hill._report_to_hill(["push failed"])  # hill-ops has gone: nothing happens
    finally:
        shutil.rmtree(folder)
    assert [(m["method"], m["params"]) for m in received] == [("hello", {"app": "palace"}), ("error", {"what": "push failed"})]


def test_markdown_links_count_in_the_map(env):
    root = env.root / "vault" / "Root.md"
    root.write_text(root.read_text() + "See [garden](projects/garden.md) and [plans](plans/next.md).\n")
    result = drive("down", "down")  # Root
    assert "links to Projects, garden, plans/next.md (no note yet)" in result["preview_title"]
    # Root → Projects → garden, then garden (linked directly) → Projects, then the
    # missing note. The map opens on the first garden, three lines above it.
    result = drive("m", "down", "down", "down")
    assert result["map"][-1] == (1, "plans/next.md  no note yet")
    assert result["list_keys"][0] == "create note"
    drive("m", "down", "down", "down", "e")
    assert (env.root / "vault" / "plans" / "next.md").exists()


def test_closed_folders_stay_closed(env):
    # Close projects/ (the cursor starts on garden, inside it), then edit Root.
    result = drive("up", "enter", "down", "down", "e")
    assert env.launched[-1][1].endswith("Root.md")
    assert (1, "projects/") in result["visible"]
    assert not any("garden" in label for _, label in result["visible"])
    prefs = json.loads((env.root / "palace" / "settings.json").read_text())
    assert prefs["notes"]["collapsed"] == ["projects"]
    # Remembered next time, and opened again while a search needs it.
    assert not any("garden" in label for _, label in drive()["visible"])
    assert any("garden" in label for _, label in drive("slash", "type:ga", "enter")["visible"])


def test_map_branches_stay_as_left_while_editing(env):
    # Close Projects' branch, then edit Root: it stays closed.
    result = drive("m", "up", "left", "up", "e")
    assert env.launched[-1][1].endswith("Root.md")
    assert result["map"] == [(0, "Root"), (1, "Projects  1 link")]
    # Open garden's branch, then edit garden: it stays open.
    result = drive("m", "right", "e")
    assert env.launched[-1][1].endswith("garden.md")
    assert (3, "↺ Projects") in result["map"]


def test_settings_open_in_hills_strip_where_the_focus_is(env, strip):
    drive("comma")
    drive("tab", "comma")
    drive("tab", "tab", "comma")
    drive("m", "comma")  # the map is in the list's place
    groups = [params["group"] for method, params in strip.sent if method == "settings.open"]
    assert groups == ["list", "calendar", "preview", "list"]


def test_a_setting_changed_in_hill_applies_at_once(env, strip):
    def change(app):  # what hill-ops does: write the file, then tell palace
        prefs = env.root / "palace" / "settings.json"
        prefs.parent.mkdir(exist_ok=True)
        prefs.write_text(json.dumps({"notes": {"calendar": False}}))
        strip.handlers["settings.changed"]({"group": "calendar", "key": ["notes", "calendar"], "value": False})

    assert not drive()["calendar_hidden"]
    assert drive(change)["calendar_hidden"]


def test_a_click_in_hills_strip_runs_only_palaces_own_keys(env, strip):
    result = drive(
        lambda app: strip.handlers["run"]({"action": "app.settings"}),
        lambda app: strip.handlers["run"]({"action": "app.exit"}),  # not one of palace's hints
    )
    assert ("settings.open", {"group": "list"}) in strip.sent
    assert result["running"]


def test_outside_hill_settings_run_on_their_own(env):
    drive("tab", "comma")
    assert env.launched[-1] == [sys.executable, "-m", "hill_ops", "settings", "calendar"]


def test_projects_without_notes_are_listed(env):
    (env.root / "vault" / "papers").mkdir()
    visible = drive()["visible"]
    assert (1, "papers/  no notes") in visible
    # n on it writes the project's first note there.
    keys = ["up"] * 5 + ["n", "type:Reading list", "enter"]  # papers/ is the first folder
    drive(*keys)
    assert (env.root / "vault" / "papers" / "Reading list.md").exists()


def test_micro_runs_in_the_project_folder_without_a_repository(env):
    drive("e")  # garden, in projects/, which isn't a repository here
    assert env.cwds[-1] == env.root / "vault" / "projects"


@pytest.fixture
def terminal(monkeypatch):
    """palace in a terminal, outside hill-ops, with hill-ops installed: records the
    runs of hill-ops that would start palace inside it, each ending with the
    next of `terminal.statuses`."""
    runs = []
    statuses = [0]
    monkeypatch.delenv("HILL_SOCKET", raising=False)
    monkeypatch.delenv("PALACE_NO_HILL", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(hill, "_hill_installed", lambda: True)

    def call(argv, env):
        runs.append(argv)
        # The palace inside says which instance hill-ops runs in, as it starts.
        inside = {hill.AGAIN: env[hill.AGAIN], "HILL_SOCKET": "/x.sock", "HILL_INSTANCE": f"/i/{len(runs)}-x"}
        with mock.patch.dict(os.environ, inside):
            hill.into_hill([])
        return statuses.pop(0)

    monkeypatch.setattr(subprocess, "call", call)
    return SimpleNamespace(runs=runs, statuses=statuses)


def test_palace_starts_itself_inside_hill_and_again_to_restart_it_all(terminal):
    terminal.statuses[:] = [RESTART_ALL, 3]
    with pytest.raises(SystemExit) as ended:
        hill.into_hill(["~/notes"])
    run = ["--", str(Path(sys.executable).with_name("palace")), "_loop", "~/notes"]
    # Started again, it's in the same instance.
    assert terminal.runs == [[*hill.HILL_OPS, *run], [*hill.HILL_OPS, "--as", "1-x", *run]] and ended.value.code == 3


def test_palace_passes_which_instance_on_to_hill(terminal):
    with pytest.raises(SystemExit):
        hill.into_hill([], "resume")
    assert terminal.runs[0][:5] == [*hill.HILL_OPS, "--resume", "--"]


def test_palace_loop_runs_palace_again_to_restart_it(monkeypatch):
    runs, statuses = [], [RESTART, RESTART, 0]

    def call(argv, env):
        runs.append((argv, env[LOOP]))
        return statuses.pop(0)

    monkeypatch.setattr(subprocess, "call", call)
    monkeypatch.setattr(hill.restart.signal, "signal", lambda *_: None)  # not pytest's Ctrl-C
    with pytest.raises(SystemExit) as ended:
        hill.restart.loop(["~/notes"])
    run = ([sys.executable, "-m", "hill", "~/notes"], str(os.getpid()))
    assert runs == [run] * 3 and ended.value.code == 0


def test_a_pane_watches_its_own_code(tmp_path):
    files = own_files()
    assert Path(hill.app.__file__).resolve() in files
    assert Path(json.__file__).resolve() not in files  # the standard library
    assert not any("site-packages" in path.parts for path in files)  # installed packages
    code = tmp_path / "code.py"
    code.write_text("one")
    watch = CodeWatch()
    watch.files = {code: code.stat().st_mtime}
    assert not watch.changed()
    os.utime(code, (0, 0))
    assert watch.changed()


def changed_code(app):
    """palace's code changed, as its look every few seconds would find."""
    app.code.files = {Path("/nonexistent/palace.py"): 0.0}
    app.check_code()


def test_restart_restarts_palace_once_micro_closes(env, strip):
    result = drive(changed_code)
    assert "restart pending" in result["header"] and result["running"]
    # Opening micro, which would hold the restart back, asks first; Esc goes on.
    answer = lambda value: lambda app: app.client.handlers["answer"]({"id": strip.asked()[-1]["id"], "value": value})
    result = drive(changed_code, "e", answer(None))
    assert strip.asked()[-1]["question"] == "palace has a restart pending: Ret restarts it first, Esc goes on"
    assert env.launched[-1][0] == "micro" and result["running"]
    # Ret restarts first.
    result = drive(changed_code, "e", answer("restart"))
    assert not result["running"] and result["return_code"] == RESTART
    # While micro is open, the restart waits for it.
    micro_open = lambda app: setattr(app, "over", lambda: None)
    result = drive(changed_code, micro_open, command("restart"))
    assert "restart once micro or the shell closes" in result["header"] and result["running"]
    result = drive(changed_code, micro_open, command("restart"), lambda app: app.client.handlers["over.done"]({}))
    assert not result["running"] and result["return_code"] == RESTART


def test_a_click_on_restart_pending_in_the_top_line_restarts(env, strip):
    async def click(app, pilot):
        header = app.get_screen_notes().query_one("#header")
        at = str(header.render()).index(":restart pending")
        await pilot.click(header, offset=(1 + at + 3, 0))  # its left padding, then into the words

    result = drive(changed_code)
    assert "· :restart pending ·" in result["header"]
    result = drive(changed_code, click, size=(300, 36))  # the test's folder makes a long top line
    assert not result["running"] and result["return_code"] == RESTART


def test_restart_with_nothing_pending_does_nothing_but_all_restarts_everything(env, strip):
    assert drive(command("restart"))["running"]
    result = drive(command("restart all"))
    assert not result["running"] and result["return_code"] == RESTART_ALL


@pytest.mark.parametrize("why", ["inside hill-ops", "opted out", "no terminal", "no hill-ops"])
def test_palace_stays_on_its_own_when(terminal, monkeypatch, why):
    if why == "inside hill-ops":
        monkeypatch.setenv("HILL_SOCKET", "/tmp/x.sock")
    elif why == "opted out":
        monkeypatch.setenv("PALACE_NO_HILL", "1")
    elif why == "no terminal":
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    else:
        monkeypatch.setattr(hill, "_hill_installed", lambda: False)
    hill.into_hill([])
    assert terminal.runs == []


def test_help_opens_in_the_strip_on_the_part_with_focus(env, strip):
    drive("question_mark")
    drive("tab", "question_mark")
    drive("tab", "tab", "question_mark")
    drive("m", "question_mark")
    sections = [params["section"] for method, params in strip.sent if method == "help.open"]
    assert sections == ["List", "Calendar", "Preview", "Map"]


def test_colon_opens_the_command_line(env, strip):
    drive("colon")
    assert ("command.open", {}) in strip.sent


def test_space_opens_the_key_tree_in_the_list_and_the_map(env, strip):
    drive("space")
    drive("m", "space")
    assert [method for method, _ in strip.sent if method == "tree.open"] == ["tree.open", "tree.open"]
    assert strip.tree == [branch for branch in hill.app.TREE if branch[0] != "s"]  # no sync or scan here
    assert ("Space", "keys", "hill.tree") in strip.hello[2]


def test_alt_c_opens_the_panel_on_claude_where_the_focus_is(env, strip):
    drive("alt+c")
    drive("tab", "tab", "alt+c")
    assert [params for method, params in strip.sent if method == "claude.open"] == [{"group": "list"}, {"group": "preview"}]
    assert ("Alt-c", "Claude", "hill.claude") in strip.hello[2]  # a click on the strip goes to hill-ops itself
    assert strip.docs == str(README) and "## The links map" in README.read_text()


def command(line):
    """What hill-ops sends when `line` is entered on its command line."""
    return lambda app: app.client.handlers["command"]({"line": line})


def test_commands_from_the_strip_run_in_palace(env, strip):
    result = drive(command("search ro"))
    assert result["titles"] == ["Projects", "Root"]
    drive(command("new Ideas"))
    assert (env.root / "vault" / "projects" / "Ideas.md").exists()  # the folder under the cursor
    assert env.launched[-1][1].endswith("Ideas.md")
    drive(command("settings calendar"))
    assert ("settings.open", {"group": "calendar"}) in strip.sent
    assert drive(command("no-such-command"))["running"]
    assert not drive(command("quit"))["running"]


def work_items(env, *items):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    for name, status in items:
        (work / f"{name}.md").write_text(f"---\nstatus: {status}  # kept\nsince: 2026-10-01\ncreated: 2026-10-01\n---\n# {name}\n")
    return work


def errors(strip):
    return [p["what"] for m, p in strip.sent if m == "error"]


def test_commands_select_an_item_and_set_its_status(env, strip):
    work = work_items(env, ("001-ask", "waiting"), ("002-go", "ready"))
    today = date.today().isoformat()
    result = drive(command("select projects 2"))
    assert result["current"] == "002-go"
    assert drive(command("select projects/work/001-ask"))["current"] == "001-ask"
    # The item named, or the one selected; since: changes with the status.
    result = drive(command("select projects 002"), command("set-status doing projects 001"), command("set-status DONE"))
    assert result["current"] == "002-go"
    assert (work / "001-ask.md").read_text() == f"---\nstatus: doing\nsince: {today}\ncreated: 2026-10-01\n---\n# 001-ask\n"
    assert (work / "002-go.md").read_text().startswith(f"---\nstatus: done\nsince: {today}\n")
    (work / "002-go.md").write_text("---\nstatus: done\nsince: 2026-10-01\n---\n")
    drive(command("set-status done projects/work/002-go.md"))  # the same status: since: stays
    assert (work / "002-go.md").read_text() == "---\nstatus: done\nsince: 2026-10-01\n---\n"
    # Not a work item, no such item, no such status: nothing changes.
    root = (env.root / "vault" / "Root.md").read_text()
    drive(command("select Root"), command("set-status done"), command("select projects 9"), command("set-status later"))
    assert (env.root / "vault" / "Root.md").read_text() == root
    assert errors(strip) == [":set-status is for work items", ":select found no note"]


def test_work_starts_the_items_session_in_claudes_pane(env, strip, monkeypatch):
    work_items(env, ("001-ask", "waiting"), ("002-go", "ready"), ("003-busy", "doing"))
    work, projects = env.root / "vault" / "projects" / "work", str(env.root / "vault" / "projects")
    tmux = []
    monkeypatch.setattr(hill.app.PalaceApp, "tmux", lambda self, *args: tmux.append(args) or "")
    started = lambda app: claude._write_text(claude.state_file(str(work / "001-ask.md")), json.dumps(
        {"key": str(work / "001-ask.md"), "pane": "%9", "state": None}))  # its program says where it runs
    result = drive(started, command("work projects 1"), command("select Root"), command("work"))
    assert result["current"] == "Root"
    spec = [p["claude"] for m, p in strip.sent if m == "panes" and p["claude"]][0]
    assert spec["name"] == "Claude on projects 001" and spec["cwd"] == projects
    assert spec["argv"][3:6] == ["_claude", str(work / "001-ask.md"), projects]
    assert tmux == [("send-keys", "-t", "%9", "-l", "go"), ("send-keys", "-t", "%9", "Enter"), ("select-pane", "-t", "%9")]
    assert errors(strip) == [":work is for work items"]
    # One Claude is busy on only shows.
    tmux.clear()
    drive(sessions({str(work / "003-busy.md"): "answering"}, pane="%7"), command("work projects 3"))
    assert [args for args in tmux if args[0] != "display"] == [("select-pane", "-t", "%7")]


def test_an_idle_session_on_a_doing_item_is_asked_to_write_it_back_once(env, strip, monkeypatch):
    work = work_items(env, ("001-doing", "doing"), ("002-waits", "waiting"), ("003-shown", "doing"))
    tmux, focused = [], {"%3"}
    monkeypatch.setattr(hill.app.PalaceApp, "tmux", lambda self, *args: tmux.append(args) or (
        "11" if args[0] == "display" and args[3] in focused else ""))
    now = time.time()
    idle = {"asked": now - 1800, "since": now - claude.WRITE_BACK_AFTER - 1}

    def idle_sessions(app):
        for name, pane in [("001-doing", "%1"), ("002-waits", "%2"), ("003-shown", "%3")]:
            key = str(work / f"{name}.md")
            claude._write_text(claude.state_file(key), json.dumps({"key": key, "state": "idle", "pane": pane} | idle))
        app.check_claude()

    sent = lambda: [args[2] for args in tmux if args[:4] == ("send-keys", "-t", args[2], "-l")]
    # Only the doing item's, not the waiting one's, nor the one whose pane has the focus;
    # once, however often palace looks.
    drive(idle_sessions, lambda app: app.check_claude(), lambda app: app.check_claude())
    assert sent() == ["%1"]
    assert ("send-keys", "-t", "%1", "-l", claude.WRITE_BACK) in tmux and ("send-keys", "-t", "%1", "Enter") in tmux
    # Not yet idle long enough: nothing.
    tmux.clear()
    idle["since"] = now - 60
    drive(idle_sessions)
    assert sent() == []
    # Committed since your question: written back already.
    vault = env.root / "vault"
    git(vault, "init", "-q")
    git(vault, "add", "projects/work/001-doing.md")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "write back")
    idle["since"] = now - claude.WRITE_BACK_AFTER - 1
    drive(idle_sessions)
    assert sent() == []


async def rest(app, pilot):
    """Stay on the note selected long enough for Claude's pane to follow."""
    await pilot.pause(hill.app.CLAUDE_AFTER + 0.2)


def test_claudes_pane_follows_the_note_selected(env, strip):
    work = work_items(env, ("001-ask", "waiting"), ("002-go", "ready"))
    one, two = str(work / "001-ask.md"), str(work / "002-go.md")
    drive(command("select projects 1"), rest, command("select projects 2"), rest, command("select projects 1"), rest)
    shown = [p["claude"]["name"] for m, p in strip.sent if m == "panes" and p["claude"]]
    assert shown[-3:] == ["Claude on projects 001", "Claude on projects 002", "Claude on projects 001"]
    # Claude never started on them, so there was nothing to keep out of sight.
    ended = [p["names"] for m, p in strip.sent if m == "panes.end"]
    assert ["Claude on projects 001"] in ended and ["Claude on projects 002"] in ended
    # Once Claude has started on one, it keeps running out of sight.
    strip.sent.clear()
    started = sessions({one: "idle"})
    drive(command("select projects 1"), rest, started, command("select projects 2"), rest)
    assert ["Claude on projects 001"] not in [p["names"] for m, p in strip.sent if m == "panes.end"]
    # Holding ↓ doesn't start a program for every note on the way.
    strip.sent.clear()
    drive(command("select projects 1"), command("select projects 2"), command("select Root"), rest)
    assert [p["claude"]["name"] for m, p in strip.sent if m == "panes" and p["claude"]] == ["Claude on vault"]
    assert claude.shown() == (str(env.root / "vault"), str(env.root / "vault"))
    assert two not in claude.read_states()


def test_claudes_pane_offers_no_session_on_an_item_done_and_pushed(env, strip):
    work_items(env, ("001-ask", "waiting"), ("002-shipped", "done"))  # not in git: done and pushed
    drive(command("select projects 1"), rest, command("select projects 2"), rest)
    specs = {p["claude"]["name"]: p["claude"]["argv"] for m, p in strip.sent if m == "panes" and p["claude"]}
    assert "--done" not in specs["Claude on projects 001"]
    assert specs["Claude on projects 002"][7] == "--done"  # palace would wrap it at once


def test_claudes_pane_offers_no_session_on_a_mirrors_notes(env, strip, work_scan):
    work_items(env, ("001-ask", "waiting"))
    project = env.root / "vault" / "projects"
    work_scan.parent.mkdir(exist_ok=True)
    (work_scan.parent / "projects-sync.json").write_text(json.dumps(
        {"synced": "2026-10-05T16:43Z", "mirrors": {str(project): "build-box"}}))
    drive(command("select projects 1"), rest, command("select Root"), rest)
    specs = {p["claude"]["name"]: p["claude"]["argv"] for m, p in strip.sent if m == "panes" and p["claude"]}
    assert specs["Claude on projects 001"][7:9] == ["--mirror", "build-box"]  # a session would write session:
    assert "--mirror" not in specs["Claude on vault"]


def test_once_claude_ends_its_session_is_offered_again(env, strip):
    work = work_items(env, ("001-ask", "waiting"))
    one = str(work / "001-ask.md")
    ended = lambda app: (claude.forget(one), app.check_claude())  # as its SessionEnd hook does
    drive(command("select projects 1"), rest, sessions({one: "idle"}), ended)
    panes = [(m, p) for m, p in strip.sent if m.startswith("panes")]
    assert [m for m, _ in panes][-2:] == ["panes.end", "panes"]
    assert panes[-1][1]["claude"]["name"] == "Claude on projects 001"
    # One closed before Claude started (Ctrl-d) leaves the place empty.
    strip.sent.clear()
    drive(command("select projects 1"), rest, sessions({one: None}), ended)
    assert [m for m, p in strip.sent if m == "panes.end"] == [] and claude.shown() is None


def test_claudes_pane_shows_the_same_session_after_a_restart(env, strip, monkeypatch):
    work = work_items(env, ("001-ask", "waiting"))
    one, projects = str(work / "001-ask.md"), str(env.root / "vault" / "projects")
    claude._write_text(claude.state_file(one), json.dumps({"key": one, "pane": "%9", "pid": 7, "state": "idle"}))
    claude.keep_shown(one, projects)
    monkeypatch.setattr(PalaceApp, "claude_runs", lambda self, entry: True)  # hill-ops kept it running
    drive()
    first = next(p for m, p in strip.sent if m == "panes")
    assert first["claude"]["name"] == "Claude on projects 001"
    # Its program gone, such as with hill-ops, it's forgotten.
    strip.sent.clear()
    monkeypatch.setattr(PalaceApp, "claude_runs", lambda self, entry: False)
    drive()
    assert next(p for m, p in strip.sent if m == "panes")["claude"] is None
    assert not claude.state_file(one).exists()


def test_without_sync_and_scan_scripts_palace_offers_nothing_of_theirs(env, strip, work_scan):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    (work / "001-ask.md").write_text("---\nstatus: waiting\n---\n")
    work_scan.parent.mkdir()
    work_scan.write_text(json.dumps({"scanned": "2026-10-05T16:43:00Z", "items": [
        {"path": "projects/work/001-ask.md", "due": True}]}))
    (work_scan.parent / "projects-sync.json").write_text(json.dumps(
        {"synced": "2026-10-05T16:43Z", "stuck": [{"repo": "projects", "note": "fetch failed"}]}))
    result = drive()
    assert {name for name, *_ in strip.commands} == {name for name, *_ in COMMANDS} - {"sync", "scan"}
    assert not {"⏳", "🤍", "⚠"} & {key for _, keys in strip.help for key, _ in keys}
    assert "s" not in [key for key, *_ in strip.tree]
    assert (1, "projects/  ● 1 waiting") in result["visible"]
    assert (3, "001-ask  ● waiting") in result["visible"]
    overview = [p for m, p in strip.sent if m == "overview"][-1]
    assert overview["where"] == "Work items that want something"
    assert ["".join(span[0] for span in line["text"]) for line in overview["lines"]] == ["● projects 001 001-ask"]


def test_sync_and_scan_run_the_folders_scripts(env, strip, work_scan):
    vault = env.root / "vault"
    drive(command("sync"))
    assert errors(strip) == ["no sync script in the folder"]
    script = vault / "scan"
    script.write_text("#!/bin/sh\necho \"work items: 0 due\" \"$@\" > ran\n")
    script.chmod(0o755)
    drive(command("scan"))
    assert (vault / "ran").read_text() == "work items: 0 due\n"
    (vault / "sync").write_text("#!/bin/sh\necho pulled \"$@\"; exit 1\n")
    (vault / "sync").chmod(0o755)
    drive(command("sync"))
    assert errors(strip)[1:] == ["sync failed"]


def test_claude_selects_a_note_through_palace_select(env, strip):
    vault = env.root / "vault"
    projects = str(vault / "projects")

    def select(path):
        def act(app):
            claude._write_text(claude.state_file(projects), json.dumps({"key": projects, "cwd": projects, "pane": "%9"}))
            claude.select_file(projects).write_text(f"{path}\n")  # as Claude writes $PALACE_SELECT
            app.check_claude()
        return act

    result = drive("m", select(vault / "Root.md"))
    assert result["current"] == "Root" and not result["in_map"]
    assert result["preview_title"].startswith("Root")
    assert drive(select("../Projects.md"))["current"] == "Projects"  # relative: from Claude's project
    # One the list hides, or one palace doesn't have, says so, and the cursor stays.
    result = drive("slash", "type:ga", "enter", select(vault / "Root.md"), select(vault / "nowhere.md"),
                   select("/etc/passwd"))
    assert result["current"] == "garden"
    assert [p["what"] for m, p in strip.sent if m == "error"] == [
        "Claude asked to select a note the list hides",
        "Claude asked to select a note palace doesn't have",
        "Claude asked to select a note palace doesn't have",
    ]


def test_a_note_claude_selects_leaves_claudes_pane_on_the_session_that_asked(env, strip):
    work = work_items(env, ("001-ask", "doing"), ("002-go", "ready"))
    one, two = str(work / "001-ask.md"), str(work / "002-go.md")

    def moves_on(app):  # 001's Claude writes 002 back and selects it
        claude.select_file(one).write_text(f"{two}\n")
        app.check_claude()

    result = drive(command("select projects 1"), rest, sessions({one: "idle"}), moves_on, rest,
                   sessions({one: "answering"}), rest)  # a reload on it doesn't follow either
    assert result["current"] == "002-go" and result["preview_title"].startswith("002-go")
    assert [p["claude"]["name"] for m, p in strip.sent if m == "panes" and p["claude"]][-1] == "Claude on projects 001"
    assert claude.shown()[0] == one
    # Selected by hand, after another, it follows again.
    strip.sent.clear()
    drive(command("select projects 1"), rest, command("select projects 2"), rest)
    assert [p["claude"]["name"] for m, p in strip.sent if m == "panes" and p["claude"]][-1] == "Claude on projects 002"


def test_errors_go_to_hills_log_in_palaces_words(env, strip):
    projects = env.root / "vault" / "projects"
    projects.chmod(0o555)  # new notes can't be written there
    try:
        drive(command("new Secret plans"))
    finally:
        projects.chmod(0o755)
    assert [params for method, params in strip.sent if method == "error"] == [
        {"what": "can't create a note: Permission denied"},  # not the title
    ]


def test_prompts_are_asked_on_the_strip(env, strip):
    drive("n", lambda app: app.client.handlers["answer"]({"id": strip.asked()[-1]["id"], "value": "Ideas"}))
    assert strip.asked()[-1] == {"id": 1, "question": "New note title (in projects/)", "value": ""}
    assert (env.root / "vault" / "projects" / "Ideas.md").exists()
    drive("down", "down", "down", "r", lambda app: app.client.handlers["answer"]({"id": strip.asked()[-1]["id"], "value": None}))
    assert strip.asked()[-1]["question"] == "Rename “Root” to" and strip.asked()[-1]["value"] == "Root"
    assert (env.root / "vault" / "Root.md").exists()  # cancelled: not renamed


def test_inside_hill_micro_runs_over_the_side_panes_and_palace_stays(env, strip, monkeypatch):
    monkeypatch.setattr(PalaceApp, "run_program", RUN_PROGRAM)
    monkeypatch.setattr(hill.app.shutil, "which", lambda name: f"/bin/{name}")
    (env.root / "vault" / "projects" / "garden.md").write_text("# garden\n\nEdited.\n")

    def exited(app):  # what hill-ops says once micro has exited
        assert app.over is not None
        strip.handlers["over.done"]({"status": 0})

    result = drive("e", "e", exited)  # the second e while micro runs: one at a time
    overs = [params for method, params in strip.sent if method == "over"]
    assert overs == [{"name": "micro", "argv": ["/bin/micro", str(env.root / "vault" / "projects" / "garden.md")],
                      "cwd": str(env.root / "vault" / "projects")}]
    assert result["current"] == "garden" and "Edited." in result["preview"]  # read again, on the same note


def hover(where, x, y):
    """A move of the mouse to column x, row y of the widget at `where`."""
    async def move(app, pilot):
        await pilot.hover(where, offset=(x, y))
    return move


def parts(seen):
    """Records which part has the focus, which parts are lit (the lighter
    background) and which key lines are highlighted."""
    def look(app):
        notes = app.get_screen_notes()
        lit = [pane.id for pane in notes.query(".pane") if pane.background_colors[1].hex == "#272727"]
        keys = [line.id for line in notes.query(Keyline) if line.has_class("-active")]
        seen.append((notes.focused_pane.id if notes.focused_pane else None, lit, keys))
    return look


def test_the_focus_follows_the_mouse_from_part_to_part_lazily(env):
    seen = []
    drive(
        parts(seen),
        hover("#calendar", 5, 3), parts(seen),  # into the calendar
        "shift+tab", hover("#calendar", 6, 3), parts(seen),  # a key back to the list; a nudge leaves it there
        hover("#calendar", 9, 5), parts(seen),  # a move takes it to the calendar again
        hover("#notes", 5, 2), parts(seen),
    )
    list_, calendar = ("list-pane", ["list-pane"], ["list-keys"]), ("calendar-pane", ["calendar-pane"], ["calendar-keys"])
    assert seen == [list_, calendar, list_, calendar, list_]


def test_inside_hill_the_mouse_takes_the_focus_from_the_panes_beside_palace(env, strip, monkeypatch):
    hill_says = [False, True]
    monkeypatch.setattr(hill_client, "take_focus", lambda: hill_says.pop(0))
    monkeypatch.setattr(hill_client.Hover, "RETRY", 0)
    seen = []
    drive(
        lambda app: app.post_message(events.AppBlur()), parts(seen),  # the focus went to Claude's pane, say
        hover("#calendar", 3, 2), hover("#calendar", 8, 5), parts(seen),  # hill-ops says no: its strip has the focus
        hover("#calendar", 12, 6), parts(seen),
    )
    assert seen == [(None, [], []), (None, [], []), ("calendar-pane", ["calendar-pane"], ["calendar-keys"])]
    assert hill_says == []


def test_the_timer_shows_when_a_sessions_cache_goes_cold(env, strip):
    work = env.root / "vault" / "projects" / "work"
    work.mkdir()
    ages = {"001-warm": 10, "002-soon": 50, "003-cold": 120, "004-fresh": 5}
    for name in ages:
        (work / f"{name}.md").write_text(f"---\nstatus: open\n---\n# {name}\n")
    seen = {}

    def look(app):
        notes = app.get_screen_notes()
        seen["timers"] = {name: notes.timer(str(work / f"{name}.md"), "t") for name in ages}
        seen["waiting"] = notes.mark_styles()["waiting"]
        overview = [p for m, p in strip.sent if m == "overview"][-1]
        seen["overview"] = [[span for span in line["text"]] for line in overview["lines"]]

    drive(*(sessions({str(work / f"{name}.md"): "idle"}, since=time.time() - minutes * 60)
            for name, minutes in ages.items()), look)
    timers, waiting = seen["timers"], seen["waiting"]
    # Warm, as before; orange in the last quarter hour; dim after ❄ once cold.
    assert timers["001-warm"] == ("t", "dim") and timers["004-fresh"] == ("t", "dim")
    assert timers["002-soon"] == ("t", waiting)
    assert timers["003-cold"] == ("❄ t", "dim")
    # The Overview shows the timers, and the sessions soonest to go cold first, the cold ones last.
    lines = ["".join(span[0] for span in line) for line in seen["overview"]]
    assert lines == ["● projects 002 002-soon ✦ 50m", "● projects 001 001-warm ✦ 10m",
                     "● projects 004 004-fresh ✦ 5m", "● projects 003 003-cold ✦ ❄ 2h"]
    assert seen["overview"][0][-1][1] == waiting
