"""palace: a notes screen for a Markdown vault.

The notes list, the activity calendar and the preview each have their own
key line at the bottom, micro-style, showing what can be done there. Notes
open in micro. Inside hill-ops, the settings are in hill-ops's strip, and the
preview runs in a pane of its own beside palace (see side.py), where micro
runs too, over it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Markdown, Static, Tree
from textual.widgets.tree import TreeNode

from keyline import Key, Keyline
from mdvault import LinkGraph, Note, Vault
from settings_panel import JsonStore, OverlayStore, StoreError
from hill_client import Client, Hover

from .calendar import ActivityCalendar
from .claude import (WORK, WRITE_BACK, cold_at, forget, keep_shown, pane_name, pane_spec, read_states, selected, shown, state_of, talk_key,
                     wants_write_back)
from .config import PROFILE, README, palace_config_path, state_home, vault_path
from .restart import CHECK_SECONDS, LOOP, RESTART, RESTART_ALL, CodeWatch
from .side import FOCUSED, PENDING, ROLES, Sides, copy_selection, heading, restart_hint
from .sync import done_states, git_state, last_commit_at, last_commits, repo_root

# Most useful first: hints that don't fit the pane are dropped from the end.
LIST_KEYS = [
    Key("e", "edit", "edit"),
    Key("n", "new", "new_note"),
    Key("/", "search", "search"),
    Key("m", "map", "toggle_map"),
    Key(",", "settings", "app.settings"),
    Key("q", "quit", "app.quit"),
    Key("w", "in progress", "work"),
    Key("z", "zoom", "zoom"),
    Key("r", "rename", "rename"),
    Key("Tab", "next pane", "app.focus_next"),
]
MAP_KEYS = [
    Key("e", "edit", "edit"),
    Key("Ret→←", "open/close"),
    Key("g", "map from here", "map_here"),
    Key("h", "home", "map_home"),
    Key("m", "list", "toggle_map"),
    Key(",", "settings", "app.settings"),
    Key("q", "quit", "app.quit"),
]
# On a link to a note that doesn't exist yet, e creates it.
MISSING_KEYS = [Key("e", "create note", "edit"), *MAP_KEYS[1:]]
HOME_TITLES = ("root", "home", "index", "start here")
CALENDAR_KEYS = [
    Key("←→↑↓", "day"),
    Key("Ret", "notes that day", "pick_day"),
    Key("c", "hide", "toggle_calendar"),
    Key("Tab", "next pane", "app.focus_next"),
]
PREVIEW_KEYS = [
    Key("↑↓", "scroll"),
    Key("e", "edit", "edit"),
    Key("f", "full", "full_preview"),
    Key("p", "hide", "toggle_preview"),
    Key("Tab", "next pane", "app.focus_next"),
]
CLEAR_FILTER = Key("Esc", "all notes", "clear_filter")
# Keys for the whole app. Inside hill-ops they show in its strip, not under the
# panes, and a click there runs them here.
APP_KEYS = [
    (",", "settings", "app.settings"),
    ("?", "help", "app.help"),
    (":", "command", "app.command"),
    ("!", "shell", "app.shell"),
    ("Tab", "next pane", "app.focus_next"),
    ("q", "quit", "app.quit"),
    ("Alt-c", "Claude", "hill.claude"),  # hill-ops acts on a click itself
    # Last, as it works in the list and the map only: a narrow strip drops it first.
    ("Space", "keys", "hill.tree"),  # hill-ops opens the key tree on a click itself
]
APP_ACTIONS = {action for _, _, action in APP_KEYS}
SCRIPTS = ("sync", "scan")
"""The scripts of ~/projects that palace runs and reads, where the folder
has them: `:sync` and `:scan`, the Overview's lines from them, and the
list's ⏳, 🤍 and ⚠. A folder without them is offered none of that."""
SCRIPT_SECONDS = 300
"""How long `:sync` or `:scan` may run before palace gives up on it."""
SCRIPT_LINES = 12
SCRIPT_NOTICE = 20
"""How many of its lines, and for how many seconds, palace shows of what
`:sync` or `:scan` said."""
ITEM_STATUSES = ("open", "ready", "doing", "waiting", "running", "done", "dropped")
"""The statuses a work item can have (the README's What a work item is)."""
# For hill-ops's command line: name, arguments, what it does, and the choices for
# its argument where there's a fixed list.
COMMANDS = [
    ("new", "[TITLE]", "write a new note, in the folder under the cursor"),
    ("rename", "[TITLE]", "rename the selected note (links to it aren't updated)"),
    ("edit", "", "open the selected note in micro"),
    ("search", "[TEXT]", "show only notes whose titles contain TEXT; no TEXT shows all"),
    ("status", "[STATUS]", "show only work items in progress, or with STATUS (a status or a mark)",
     ["open", "ready", "waiting", "running", "doing", "done", "dropped", "due", "dead", "decide"]),
    ("select", "ITEM", "select a note: its path in the folder, or a work item as PROJECT NNN"),
    ("set-status", "STATUS [ITEM]", "set the selected work item's status, or ITEM's, with since: today",
     list(ITEM_STATUSES)),
    ("work", "[ITEM]", "the selected work item's session in Claude's pane, or ITEM's: Claude starts on it"),
    ("sync", "", "run the folder's sync script with --pull: what's out of step, pulled, and the work items scanned"),
    ("scan", "", "run the folder's scan script: which waits are over, which jobs died"),
    ("map", "", "the links map, or back to the list"),
    ("home", "", "the map from the home note"),
    ("here", "", "the map from the selected note"),
    ("zoom", "", "only the last projects worked on, or them at the top (List → Zoom style), and back"),
    ("calendar", "", "hide or show the calendar"),
    ("preview", "", "hide or show the preview"),
    ("full", "", "the preview at full width, and back"),
    ("settings", "[GROUP]", "the settings, on GROUP", ["list", "calendar", "preview", "sync", "palace"]),
    ("shell", "", "your shell, in the folder"),
    ("restart", "[all]", "restart the panes whose code changed, each once it's idle; all: hill-ops and everything in it",
     ["all"]),
    ("quit", "", "quit palace; changed notes are committed and pushed"),
]
# For hill-ops's key tree, opened with Space: each command that has no key of
# its own, by letters. The status letters are the same under t and f.
_STATUS_KEYS = (("o", "open"), ("g", "ready"), ("d", "doing"), ("w", "waiting"), ("u", "running"),
                ("f", "done"), ("h", "dropped"))
TREE = [
    ("t", "set status", [(key, status, f"set-status {status}") for key, status in _STATUS_KEYS]),
    ("f", "show only", [
        ("p", "in progress", "status"),
        *[(key, status, f"status {status}") for key, status in _STATUS_KEYS],
        ("e", "due", "status due"),
        ("j", "dead", "status dead"),
        ("q", "decide", "status decide"),
    ]),
    ("w", "work on it", "work"),
    ("v", "view", [("z", "zoom", "zoom")]),
    ("s", "sync, scan", [("s", "sync", "sync"), ("c", "scan", "scan")]),
    ("r", "restart", [("r", "changed panes", "restart"), ("a", "all", "restart all")]),
]
# For hill-ops's help: what each key does, part by part.
HELP = [
    ("List", [
        ("e", "open the note in micro, where Alt-c talks to Claude"),
        ("Ret", "open or close the folder"),
        ("n", "a new note, in the folder under the cursor"),
        ("r", "rename the note (links to it aren't updated)"),
        ("/", "search titles; Esc shows all notes again"),
        ("w", "only work items in progress, and back; Esc shows all notes again"),
        ("z", "zoom on the last projects worked on (List → Zoom on), those that count as one together; "
              "z again shows the whole list"),
        ("m", "the links map"),
        ("g", "the map from the selected note"),
        ("h", "the map from the home note"),
        ("click", "select, and open or close a folder, as Ret does"),
    ]),
    ("Map", [
        ("Ret", "open or close a note's links"),
        ("→ ←", "open or close a note's links, or go up"),
        ("e", "open the note, or create one that doesn't exist yet"),
        ("click", "select, and open or close its links, as Ret does"),
        ("g", "the map from here, with who links to it at the top"),
        ("h", "back to the home note"),
        ("m", "back to the list, on the same note"),
    ]),
    ("Work items", [
        ("● open", "pink: soon ready, still without a plan"),
        ("● ready", "red: waits for your go"),
        ("● waiting", "orange: waits for your answer"),
        ("● running", "yellow: waits for something to finish"),
        ("● to commit", "green: done, not committed"),
        ("● to push", "blue: committed, not pushed"),
        ("● done", "violet: all done"),
        ("● sample", "grey: a sample, work/NNN-name-sample.md, made up to show the pattern; counted nowhere"),
        ("⏳", "after the mark: its wait is over (./scan)"),
        ("🤍", "after the mark: the job it waits on died (./scan)"),
        ("❓N", "after the mark: N decisions wait on you, in its Before section"),
        ("✦", "Claude's pane has a session on it; ✦ asks: Claude asks you something there"),
        ("✦ (white)", "on a project's README, else its folder: Claude's pane has a session on the project"),
        ("12m", "at the right: how long that session has been idle, or asking"),
        ("⚠", "on a project's folder: the folder's sync couldn't fetch or pull its repo"),
    ]),
    ("Calendar", [
        ("← →", "a week back or on"),
        ("↑ ↓", "a day back or on"),
        ("Ret or click", "the notes last changed that day"),
        ("Esc", "all notes again"),
        ("c", "hide or show the calendar"),
    ]),
    ("Preview", [
        ("↑ ↓", "scroll"),
        ("e", "open the note in micro"),
        ("f", "full width, and back; in its own pane beside palace, the whole window"),
        ("p", "hide or show the preview"),
        ("Ctrl-r", "in its pane beside palace, with a restart pending: restart it"),
    ]),
    ("Claude", [
        ("Ret", "start Claude Code on the note's session, with what you typed as your first question; "
                "from then on, Claude Code's own keys"),
        ("Ctrl-d", "before Claude starts: close the pane"),
    ]),
    ("palace", [
        (",", "the settings, on the part you're in, with Claude's tip under them"),
        ("?", "this help"),
        (":", "the command line"),
        ("Space", "the key tree, in hill-ops's strip: a letter for each command with no key of its own, "
                  "such as Space t d to set the item doing; the command line shows each one's keys"),
        ("!", "your shell, in the folder"),
        ("Tab", "the next pane, on to the preview's beside palace, and back"),
        (":restart pending", "in a pane's top line, with a restart pending: click it to run :restart"),
        ("mouse", "move it over a pane to give that pane the focus, which makes it lighter; "
                  "a nudge doesn't take the focus back from where a key moved it"),
        ("q or Ctrl-q", "quit; changed notes are committed and pushed"),
        ("Alt-c", "ask Claude, on the line under the settings in hill-ops's strip, or back to it"),
    ]),
]


def _move_to(tree: Tree, node: TreeNode) -> None:
    """Move a tree's cursor to `node`. move_cursor goes by the node's line,
    which is -1 until the tree has laid out lines it was given since: then
    the cursor would land on the first line. Reading its last line lays
    them out."""
    if tree.last_line >= 0:
        tree.move_cursor(node)


def _pane(widget: Widget | None) -> Widget | None:
    """The part of the notes screen `widget` is in: the pane around it."""
    if widget is None:
        return None
    return next((node for node in widget.ancestors_with_self if node.has_class("pane")), None)


def _home_relative(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


PLACE = "place.json"
"""Where palace was, in state_home(), to start there again: the note or
folder under the cursor ("select"), and the map's root ("map") if it was
in the map. Inside hill-ops, palace-place.json in hill-ops's instance
($HILL_INSTANCE) is where this instance was, and PLACE where any was
last, for a new instance to start from."""
INSTANCE = "HILL_INSTANCE"
LABEL_PROJECTS = 3
"""How many projects label palace's instance in hill-ops's list (label.json):
the last ones selected in."""
RECENT_PROJECTS = 10
"""How many of the projects selected in this hill-ops instance palace keeps, for
`z` to zoom on (settings: List → Zoom on)."""


def _instance() -> Path | None:
    """hill-ops's instance folder, inside hill-ops."""
    return Path(env) if (env := os.environ.get(INSTANCE)) else None


def _place_files() -> list[Path]:
    """Where palace keeps its place, its instance's first."""
    instance = _instance()
    return ([instance / "palace-place.json"] if instance else []) + [state_home() / PLACE]


def _read_place() -> dict:
    for path in _place_files():
        try:
            place = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(place, dict):
            return place
    return {}


def instance_prefs(path: Path) -> JsonStore | OverlayStore:
    """palace's settings in `path`, with those its profile marks `instance`
    kept in hill-ops's instance (palace.json in it), as hill-ops keeps them."""
    store = JsonStore(path)
    if (instance := _instance()) is None:
        return store
    import tomllib

    profile = tomllib.loads(PROFILE.read_text())
    own = {
        tuple(s["key"]): s["default"]
        for g in profile.get("groups", []) for s in g.get("settings", []) if s.get("instance") is True
    }
    return OverlayStore(store, instance / f"{profile['app']}.json", own) if own else store


def project_of(relpath: str | None, root: Path) -> str | None:
    """The top-level folder `relpath` is in, or is: its project, in
    ~/projects."""
    parts = Path(relpath).parts if relpath else ()
    if not parts or (len(parts) == 1 and not (root / parts[0]).is_dir()):
        return None
    return parts[0]


def one_project(a: str, b: str) -> bool:
    """Whether two projects count as one, by their names: the same but for
    their last `-part` (zinc-mcp, zinc-desk), or one is the other
    plus `-something` (atlas, atlas-overlay)."""
    if a == b or a.startswith(b + "-") or b.startswith(a + "-"):
        return True
    return "-" in a and "-" in b and a.rpartition("-")[0] == b.rpartition("-")[0]


def zoom_groups(recent: list[str], projects: list[str], count: int) -> list[list[str]]:
    """What `z` zooms on: the first `count` of the `recent` projects still
    in `projects`, most recent first, each with the projects that count as
    one with it (one_project), which take no place of their own."""
    groups: list[list[str]] = []
    for project in recent:
        if len(groups) >= count:
            break
        if project not in projects or any(project in group for group in groups):
            continue
        groups.append([project, *(p for p in projects if p != project and one_project(p, project)
                                  and not any(p in group for group in groups))])
    return groups


def _read_label() -> list[str]:
    if (instance := _instance()) is None:
        return []
    try:
        label = json.loads((instance / "label.json").read_text())
    except (OSError, ValueError):
        return []
    return [p for p in label if isinstance(p, str)] if isinstance(label, list) else []


SESSION_FILE = "palace-session.json"


def _session_file() -> Path | None:
    """Where palace keeps what it did in this hill-ops instance: in its
    folder ($HILL_INSTANCE), which lasts as long as the instance, so the
    zoom survives palace, hill-ops and the Mac restarting, and another
    instance never sees it. In hill-ops's folder for the session ($HILL_RUN)
    without an instance."""
    if (instance := _instance()) is not None:
        return instance / SESSION_FILE
    return Path(run) / SESSION_FILE if (run := os.environ.get("HILL_RUN")) else None


def _read_session() -> dict:
    paths = [path] if (path := _session_file()) else []
    # Kept in hill-ops's folder for the session until 2026-10-08: read it
    # if the instance has none yet, so a hill running then keeps its zoom.
    if _instance() is not None and (run := os.environ.get("HILL_RUN")):
        paths.append(Path(run) / SESSION_FILE)
    for path in paths:
        try:
            session = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(session, dict):
            return session
    return {}


def _write_session(session: dict) -> None:
    if (path := _session_file()) is None:
        return
    try:
        new = path.with_name(f".{path.name}.{os.getpid()}")
        new.write_text(json.dumps(session))
        new.replace(path)
    except OSError:
        pass  # z zooms on what this palace saw, as outside hill-ops


def _write_label(projects: list[str]) -> None:
    """Label hill-ops's instance with the projects, this session's first, then
    those it was labelled with before, for its list (hill-ops --resume)."""
    if (instance := _instance()) is None or not projects:
        return
    projects = [*projects, *(p for p in _read_label() if p not in projects)][:LABEL_PROJECTS]
    try:
        new = instance / f".label.json.{os.getpid()}"
        new.write_text(json.dumps(projects))
        new.replace(instance / "label.json")
    except OSError:
        pass


def scripts(root: Path) -> set[str]:
    """Which of SCRIPTS the folder has, where they can run."""
    return {name for name in SCRIPTS if os.access(root / name, os.X_OK)}


def offered(root: Path | None = None) -> tuple[list, list, list]:
    """COMMANDS, HELP and TREE for hill-ops, without what needs a script the
    folder doesn't have (SCRIPTS); all of them without a folder, as the
    reference gives them (hill.reference)."""
    missing = set() if root is None else set(SCRIPTS) - scripts(root)
    needs = {"⏳": "scan", "🤍": "scan", "⚠": "sync"}
    commands = [c for c in COMMANDS if c[0] not in missing]
    help = [(part, [(key, text) for key, text in keys if needs.get(key) not in missing]) for part, keys in HELP]
    tree = []
    for key, name, under in TREE:
        if isinstance(under, list):
            under = [entry for entry in under if entry[2] not in missing]
            if not under:
                continue
        tree.append((key, name, under))
    return commands, help, tree


def _work_scan() -> dict:
    """The state file ~/projects/scan writes ($XDG_STATE_HOME/work-scan.json,
    else ~/.local/state's), or {} without a scan."""
    folder = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    try:
        state = json.loads((folder / "work-scan.json").read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def read_work_scan(root: Path) -> dict[Path, str]:
    """What ~/projects/scan last said of work items: "dead" for one whose
    job died, "due" for one whose wait is over, by its file. Its paths are
    from the folder it scans, `root`, where the script is. Nothing without
    a scan."""
    state = _work_scan()
    marks: dict[Path, str] = {}
    for item in state.get("items") or []:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        job = item.get("job") if isinstance(item.get("job"), dict) else {}
        if job.get("state") == "dead":
            marks[(root / item["path"]).resolve()] = "dead"
        elif item.get("due") is True:
            marks[(root / item["path"]).resolve()] = "due"
    return marks


SYNC_LATE = timedelta(hours=1)
"""How old ~/projects/sync's state file gets before the Overview warns:
its timer runs every 15 minutes, so a few missed runs."""


def read_sync(now: datetime | None = None) -> dict:
    """What ~/projects/sync's last --fetch or --pull run couldn't do, from
    the state file it writes ($XDG_STATE_HOME/projects-sync.json, else
    ~/.local/state's): "stuck", (repo, note) for each repo it couldn't
    fetch or pull, and "late", when it ran and where, once that's longer
    ago than SYNC_LATE. Nothing without a file."""
    folder = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    try:
        state = json.loads((folder / "projects-sync.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    stuck = [(str(row.get("repo")), str(row.get("note"))) for row in state.get("stuck") or [] if isinstance(row, dict)]
    host = f" on {state['host']}" if isinstance(state.get("host"), str) else ""
    try:
        synced = datetime.fromisoformat(str(state.get("synced")).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return {"stuck": stuck, "late": f"never{host}"}
    late = None
    if (now or datetime.now().astimezone()) - synced > SYNC_LATE:
        day = "" if synced.date() == date.today() else f"{synced:%Y-%m-%d} "
        late = f"{day}{synced:%H:%M}{host}"
    return {"stuck": stuck, "late": late, "when": f"{synced:%H:%M}{host}"}


def read_mirrors() -> dict[Path, str]:
    """The projects this machine keeps as mirrors, worked on another machine
    only, from ~/projects/sync's state file (its "mirrors": each folder and
    the machine it's worked on). Nothing without a file, or from a sync too
    old to write them."""
    folder = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    try:
        state = json.loads((folder / "projects-sync.json").read_text())
    except (OSError, ValueError):
        return {}
    mirrors = state.get("mirrors") if isinstance(state, dict) else None
    if not isinstance(mirrors, dict):
        return {}
    return {Path(path): host for path, host in mirrors.items() if isinstance(host, str) and host}


def _overview_where(scan: bool) -> str:
    """What the Overview's lines are, and, where the folder has a scan
    script, when ~/projects/scan last looked which are due, and where."""
    where = "Work items that want something"
    if not scan:
        return where
    state = _work_scan()
    try:
        scanned = datetime.fromisoformat(str(state.get("scanned")).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return f"{where}; no scan yet"
    day = "" if scanned.date() == date.today() else f"{scanned:%Y-%m-%d} "
    host = f" on {state['host']}" if isinstance(state.get("host"), str) else ""
    return f"{where}; last scan {day}{scanned:%H:%M}{host}"


def _write_place(place: dict) -> None:
    for path in _place_files():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            new = path.with_name(f".{path.name}.{os.getpid()}")
            new.write_text(json.dumps(place))
            new.replace(path)
        except OSError:
            pass  # palace starts on the first note next time, as before


# How often palace looks whether the vault changed outside it.
POLL_SECONDS = 1.0

# How often palace looks how far done work items have got in git: a commit
# or a push changes no note, so the vault's stamp doesn't see it.
GIT_POLL_SECONDS = 3.0

# How often palace reads how Claude's sessions stand (claude.read_states),
# and how long a note must stay selected for Claude's pane to follow it, so
# holding ↓ doesn't start a program for every note on the way.
CLAUDE_SECONDS = 1.0
PARK_AFTER = 50
"""Palace → Park idle sessions after's default, in minutes: the prompt
cache's hour, less a margin, so that a session parks once its cache
can't help any more."""
CLAUDE_AFTER = 0.3
# How long `:work` waits for a session's program to say which pane it runs
# in, as tries of so many seconds.
WORK_TRIES = 25
WORK_WAIT = 0.2

# A work item's mark in the list and its colour, in a dark theme and a light
# one: a rainbow, from soon ready to all done. The first four are its
# `status:`; a done item's comes from git (done_states). Folders count those
# in FOLDER_MARKS, still in progress.
MARKS = {
    "open": ("#f783ac", "#d6336c"),  # pink: soon ready, still without a plan
    "ready": ("#ff6b6b", "#e03131"),  # red: waits for your go
    "waiting": ("#ffa94d", "#e8590c"),  # orange: waits for your answer
    "running": ("#ffd43b", "#f08c00"),  # yellow: waits for something to finish
    "to commit": ("#69db7c", "#2f9e44"),  # green: done, not committed
    "to push": ("#4dabf7", "#1971c2"),  # blue: committed, not pushed
    "done": ("#b197fc", "#7048e8"),  # violet: all done
}
FOLDER_MARKS = ("open", "ready", "waiting", "running", "to commit", "to push")
# What `:status` can show, besides the marks: statuses without one.
SCAN_MARKS = {"due": "⏳", "dead": "🤍"}
"""What ~/projects/scan says of a work item, after its status mark: its
wait is over, or the job it waits on died (which is due too, so only the
skull shows)."""
DROPPED = "\U0001F573️"
"""A dropped work item's mark, a hole, in the text's own colour: it isn't
on the way to done, so it has no place in the rainbow."""
DECIDE = "❓"
"""After a work item's status mark: how many decisions wait on you, its
unticked `- [ ] Decide:` lines in Before (`:status decide` shows those)."""
SESSION = ("#ffffff", "")
"""The mark of a session on a project, rather than a work item: a white ✦,
in a dark theme, the text's own colour in a light one, where white
wouldn't show. White isn't a status's colour, so it can't be read as one."""
SOON_COLD = 15 * 60
"""How long before a session's prompt cache goes cold its timer turns
orange (*answer soon*), or a quarter of the cache's life when that's
shorter, as the 5 minutes of overage."""
COLD = "❄"
"""Before the timer of a session whose prompt cache has gone cold: answering
it costs the whole conversation again, uncached."""
STATUSES = (*MARKS, "doing", "dropped", *SCAN_MARKS, "decide")
WORK_ITEM = re.compile(r"(?:^|/)work/(\d+)-[^/]+(?<!-sample)\.md$")
"""A work item's path in the vault: <project>/work/NNN-name.md, but for a
sample (SAMPLE)."""
SAMPLE = re.compile(r"(?:^|/)work/\d+-[^/]+-sample\.md$")
"""A sample work item's path, <project>/work/NNN-name-sample.md: one made
up to show the pattern, which palace shows grey, after *sample*, counts
nowhere and gives no session of its own."""


def _since_text(seconds: float) -> str:
    """How long, in the shortest form: 45s, 12m, 3h, 2d."""
    seconds = max(0, int(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _minutes_text(seconds: float) -> str:
    """How long, as _since_text has it, but in whole minutes, at least 1m:
    the Overview's timers, which change once a minute."""
    return _since_text(max(60, seconds - seconds % 60))


def _cache_look(cold: tuple[float, float] | None, now: float) -> str:
    """How a session's prompt cache stands, from claude.cold_at's (when it
    goes cold, its life): "warm", "soon" (within SOON_COLD of going cold)
    or "cold"; "warm" when it isn't known."""
    if cold is None:
        return "warm"
    left = cold[0] - now
    return "cold" if left <= 0 else "soon" if left <= min(SOON_COLD, cold[1] / 4) else "warm"


def _is_readme(note: Note) -> bool:
    return note.path.name.casefold() == "readme.md"


def _work_notes(notes: list[Note]) -> list[Note]:
    """The notes, with a status, its `since:` and decisions kept only on
    work items (WORK_ITEM) and samples (SAMPLE): another note's `status:`
    is another schema's field, such as atlas's maturity of a pattern, not
    work."""
    return [note if WORK_ITEM.search(note.relpath) or SAMPLE.search(note.relpath)
            else replace(note, status="", since=None, decisions=0) for note in notes]


def _is_sample(note: Note) -> bool:
    return bool(SAMPLE.search(note.relpath))
OVERVIEW = {
    "asks": "Claude asks you something in its session",
    "dead": "the job it waits on died",
    "due": "its wait is over",
    "waiting": "waits for your answer",
    "ready": "waits for your go",
    "running": "waits for something to finish",
    "live": "Claude has a session on it",
}
"""Why a work item is in the Overview, in hill-ops's panel, most pressing first."""
# How many days an item stays in the list once all done or dropped
# (settings: List → Hide done items); None keeps it.
HIDE_DONE = {"never": None, "now": 0, "day": 1, "week": 7, "month": 30}
ZOOM_SEPARATOR = "────────"
"""The line between the zoomed projects and the rest (List → Zoom style: at
the top). The list draws it across its whole width (NotesTree.render_label);
this is its label."""


class NotesTree(Tree[Note | None]):
    """The notes list. Ret and a click do the same: select, and open or
    close a folder; `e` edits, so moving around never opens micro. Space
    is the key tree's, not Tree's other way to open a folder."""

    BINDINGS = [Binding("space", "app.tree", show=False)]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.timed: set[TreeNode] = set()
        """The nodes that show a session's timer, redrawn every second."""

    def render_label(self, node: TreeNode, base_style: Style, style: Style) -> Text:
        """As Tree draws it, but the cursor's style goes under the label's
        own colours rather than over them, so a work item's mark keeps its
        colour on the line under the cursor. A folder's line gets the white
        ● of a session on its project when its label doesn't carry it, and
        a line with a session's mark gets its timer at the right edge, when
        there's room for it after the title (NotesScreen.line_session).
        The zoom's separator is a rule across the list's whole width, in
        the text's own colour, so it reads as a line and not as a gap."""
        if node.data is None and str(node.label) == ZOOM_SEPARATOR:
            width = self.scrollable_content_region.width - self._guide_width(node)
            text = Text(ZOOM_SEPARATOR[0] * max(width, len(ZOOM_SEPARATOR)))
            text.stylize_before(style)
            return text
        text = self._label(node, base_style)
        line = getattr(self.screen, "line_session", None)
        mark, clock = line(node) if line is not None else (None, None)
        if mark is not None:
            text.append_text(mark)
        if clock is not None:
            room = self.scrollable_content_region.width - self._guide_width(node) - text.cell_len
            if room >= clock.cell_len + 2:
                text.append(" " * (room - clock.cell_len))
                text.append_text(clock)
            self.timed.add(node)
        text.stylize_before(style)
        return text

    def _label(self, node: TreeNode, base_style: Style) -> Text:
        return super().render_label(node, base_style, Style())

    def get_label_width(self, node: TreeNode) -> int:
        """The label's width without the timer, which only takes the room
        left, so that it never makes the list scroll sideways; the zoom's
        separator counts as its label, for the same reason."""
        return self._label(node, Style()).cell_len

    def _guide_width(self, node: TreeNode) -> int:
        depth = 0
        while node.parent is not None:
            depth, node = depth + 1, node.parent
        return max(0, depth - (0 if self.show_root else 1)) * self.guide_depth

    def tick(self) -> None:
        """Redraw the lines with a timer, so it counts on."""
        for node in list(self.timed):
            if self._tree_nodes.get(node.id) is node and node._line >= 0:
                node.refresh()
            else:
                self.timed.discard(node)


@dataclass(frozen=True)
class Folder:
    """A folder in the notes list."""

    path: str


@dataclass
class MapItem:
    """What a node in the links map stands for."""

    kind: str
    """"note", "cycle" (a note already on the path), "missing" (a link to a
    note that doesn't exist yet) or "group" (a heading)."""
    note: Note | None = None
    target: str = ""
    path: tuple[str, ...] = ()
    """Notes from the map's root down to this one, to spot cycles."""
    file: str = ""
    """For a missing note linked by path, where to create it."""
    filled: bool = False


class MapTree(NotesTree):
    """The links map: → opens a note's links, ← closes them or goes up."""

    BINDINGS = [
        Binding("right", "open_links", show=False),
        Binding("left", "close_links", show=False),
    ]

    def action_open_links(self) -> None:
        node = self.cursor_node
        if node is None or not node.allow_expand:
            return
        if not node.is_expanded:
            node.expand()
        elif node.children:
            self.move_cursor(node.children[0])

    def action_close_links(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.is_expanded and node.children:
            node.collapse()
        elif node.parent is not None:
            self.move_cursor(node.parent)


class Prompt(ModalScreen[str | None]):
    """One line of input at the bottom of the screen."""

    DEFAULT_CSS = """
    Prompt { background: transparent; }
    Prompt #box { dock: bottom; height: auto; background: $surface; border-top: solid $accent; }
    Prompt #question { padding: 0 1; }
    Prompt #answer, Prompt #answer:focus { border: none; height: 1; padding: 0 1; }
    """
    BINDINGS = [Binding("escape", "cancel", priority=True)]

    def __init__(self, question: str, value: str = "") -> None:
        super().__init__()
        self.question = question
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.question, id="question")
            yield Input(self.value, id="answer")
            yield Keyline(Key("Ret", "ok"), Key("Esc", "cancel", "cancel"), classes="-active")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NotesScreen(Screen):
    """The notes list (or the links map) over the calendar, and the
    preview. The part with the focus has a lighter background and its key
    line highlighted; the focus follows the mouse, lazily (hill-client's
    Hover)."""

    DEFAULT_CSS = """
    #header { height: 1; background: $panel; color: $text-muted; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }
    #left { width: 1fr; }
    #list-pane { height: 1fr; }
    #calendar-pane { height: auto; border-top: solid $panel; }
    #preview-pane { width: 45%; border-left: solid $panel; }
    #search { display: none; height: 1; border: none; padding: 0 1; background: $boost; }
    #search.-shown { display: block; }
    #notes, #map { height: 1fr; background: transparent; }
    #search:focus, #notes:focus, #map:focus { background-tint: transparent; }
    #preview-title { height: auto; padding: 0 1; background: $boost; }
    #preview-scroll { height: 1fr; padding: 0 1; }
    #preview { margin: 0; }
    .-hidden { display: none; }
    """ + f".pane:focus-within {{ {FOCUSED} }}\n"

    BINDINGS = [
        Binding("n", "new_note", "New"),
        Binding("r", "rename", "Rename"),
        Binding("e", "edit", "Edit"),
        Binding("slash", "search", "Search"),
        Binding("escape", "clear_filter", show=False),
        Binding("c", "toggle_calendar", show=False),
        Binding("p", "toggle_preview", show=False),
        Binding("f", "full_preview", show=False),
        Binding("m", "toggle_map", show=False),
        Binding("w", "work", show=False),
        Binding("z", "zoom", show=False),
        Binding("g", "map_here", show=False),
        Binding("h", "map_home", show=False),
        Binding("exclamation_mark", "app.shell", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.notes: list[Note] = []
        self.place: dict = {}
        self.started_as: dict[str, str | None] = {}
        """Each live session's item's mark when palace first saw it run:
        one that turns ready or running since has handed off (park_reason)."""
        self.done_states: dict[Path, str] = {}
        """How far each done work item has got in git (done_states)."""
        self.scan: dict[Path, str] = {}
        """What ~/projects/scan says of work items (read_work_scan)."""
        self.sync: dict = {}
        """What ~/projects/sync couldn't do (read_sync), for the Overview."""
        self.commit_days: dict[Path, date] = {}
        """The day of the last commit to each done or dropped item without a
        `since:` (last_commits), while Hide done items waits a while."""
        self.work_filter: str | None = None
        """Which work items the list shows: "progress" for those in
        progress, a status or a mark, or None for all notes."""
        self.git_roots: dict[Path, Path | None] = {}
        """Where palace is, as last written to PLACE."""
        self.placed = False
        """Whether palace has moved to where it starts: until then, the
        cursor on the list's first line isn't where you are."""
        self.start: str | None = None
        self.moved = False
        """Where palace started, and whether it has moved since: the start
        isn't a project worked on in this session."""
        self.session: dict = {}
        """What this hill-ops instance worked on, as last written (_write_session)."""
        self.recent_projects: list[str] = []
        """The projects selected in this hill-ops instance, newest first
        (RECENT_PROJECTS); the first ones label hill-ops's instance."""
        self.zoom: list[list[str]] | None = None
        """The projects `z` zoomed the list on, by group (zoom_groups), kept
        for the hill-ops instance; None for the whole list."""
        self.vault_stamp: tuple | None = None
        self.graph = LinkGraph([])
        self.projects: list[str] = []
        """The vault's top-level folders, shown even without notes."""
        self.title_counts: Counter[str] = Counter()
        self.search_text = ""
        self.day: date | None = None
        self.map_root: Note | None = None
        """The note the links map starts from; None while showing the list."""
        self.collapsed: set[str] = set()
        """Folders closed in the list (kept in palace's settings)."""
        self.map_open: set[tuple] = set()
        self.map_closed: set[tuple] = set()
        """Map branches opened or closed by hand, by their path from the root."""
        self.git_line = ""
        """The header's git state, of the repository under the cursor."""
        self._git_asked: Path | None = None
        self.hover = Hover()
        """Where the mouse is, for the focus that follows it."""
        self.focused_pane: Widget | None = None
        """The part with the focus: the list's pane, the calendar's or the
        preview's; None while palace doesn't have the focus."""
        self.pane_focus: dict[str, Widget] = {}
        """What had the focus last in each part, by its pane's id, for the
        mouse to give it back."""

    @property
    def vault(self) -> Vault:
        return self.app.vault

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal():
            with Vertical(id="left"):
                with Vertical(id="list-pane", classes="pane"):
                    yield Input(placeholder="search titles", id="search")
                    yield NotesTree("notes", id="notes")
                    yield MapTree("map", id="map", classes="-hidden")
                    yield Keyline(*self.pane_keys(LIST_KEYS), id="list-keys")
                with Vertical(id="calendar-pane", classes="pane"):
                    yield ActivityCalendar(id="calendar")
                    yield Keyline(*self.pane_keys(CALENDAR_KEYS), id="calendar-keys")
            with Vertical(id="preview-pane", classes="pane"):
                yield Static(id="preview-title")
                with VerticalScroll(id="preview-scroll"):
                    yield Markdown(id="preview")
                yield Keyline(*self.pane_keys(PREVIEW_KEYS), id="preview-keys")

    def on_mount(self) -> None:
        tree = self.query_one("#notes", NotesTree)
        tree.show_root = False
        self.collapsed = set(self.pref(("notes", "collapsed"), []))
        self.apply_settings()
        # Start where palace was when it last stopped.
        place = _read_place()
        self.place = place
        select = place.get("select") if isinstance(place.get("select"), str) else None
        self.start = select
        # What this hill-ops instance worked on, and whether z zoomed on it.
        session = _read_session()
        self.session = session
        recent = session.get("recent")
        self.recent_projects = [p for p in recent if isinstance(p, str)] if isinstance(recent, list) else []
        zoom = session.get("zoom")
        if isinstance(zoom, list) and zoom and all(isinstance(g, list) and all(isinstance(p, str) for p in g) for g in zoom):
            self.zoom = zoom
        if self.vault.root.is_dir():
            notes = _work_notes(self.vault.notes())
            done = [note.path for note in notes if note.status == "done"]
            self.done_states = done_states(done, self.git_roots)
            self.commit_days = last_commits(self.undated(notes), self.git_roots)
        self.scan, self.sync = self.read_scripts()
        self.reload(select=select)
        tree.focus()
        root = next((n for n in self.notes if n.relpath == place.get("map")), None)
        if root is not None:
            self.show_map(root, select)
            self.update_header()
        # Follow notes and folders changed outside palace: by Claude, the
        # shell, Finder, a sync or a pull.
        self.set_interval(POLL_SECONDS, self.poll)
        self.set_interval(GIT_POLL_SECONDS, self.poll_git)
        # The marks' colours are for a dark theme or a light one.
        self.app.theme_changed_signal.subscribe(self, lambda _: self.reload())

    def poll(self) -> None:
        """Look, off the screen's thread, whether the vault changed since it
        was last read; if so, read it again, quietly."""
        self.run_worker(self._poll, thread=True, group="poll", exclusive=True, exit_on_error=False)

    def _poll(self) -> None:
        stamp = self.vault.stamp() if self.vault.root.is_dir() else None
        if stamp != self.vault_stamp:
            self.app.call_from_thread(self._changed_outside, stamp)

    def _changed_outside(self, stamp: tuple | None) -> None:
        if stamp == self.vault_stamp:  # read meanwhile
            return
        self.reload(quiet=True)
        self.wrap_done()  # an item may have handed off, such as turned ready

    def poll_git(self) -> None:
        """Look, off the screen's thread, how far done work items have got
        in git, when those without a `since:` were last committed, and what
        the scan says of them; if that changed, show it."""
        paths = [note.path for note in self.notes if note.status == "done"]
        undated = self.undated(self.notes)
        self.run_worker(lambda: self._poll_git(paths, undated), thread=True, group="git", exclusive=True, exit_on_error=False)

    def _poll_git(self, paths: list[Path], undated: list[Path]) -> None:
        states = done_states(paths, self.git_roots)
        days = last_commits(undated, self.git_roots)
        scan, sync = self.read_scripts()
        if states != self.done_states or days != self.commit_days or scan != self.scan or sync != self.sync:
            self.app.call_from_thread(self._git_changed, states, days, scan, sync)

    def read_scripts(self) -> tuple[dict[Path, str], dict]:
        """What the scan and the sync say (read_work_scan, read_sync), from
        those of SCRIPTS the folder has; nothing from one it doesn't."""
        have = scripts(self.vault.root)
        return (read_work_scan(self.vault.root) if "scan" in have else {},
                read_sync() if "sync" in have else {})

    def _git_changed(self, states: dict[Path, str], days: dict[Path, date], scan: dict[Path, str], sync: dict) -> None:
        self.done_states = states
        self.commit_days = days
        self.scan = scan
        self.sync = sync
        self.reload(quiet=True)
        self.wrap_done()

    def undated(self, notes: list[Note]) -> list[Path]:
        """Done or dropped items without a `since:`, while Hide done items
        waits a while: their last commit says since when."""
        if not HIDE_DONE.get(self.pref(("notes", "hide_done"), "never")):
            return []
        return [note.path for note in notes if note.status in ("done", "dropped") and note.since is None]

    # -- settings --

    def pref(self, key: tuple[str, ...], default: Any) -> Any:
        return self.app.pref(key, default)

    def apply_settings(self) -> None:
        self.query_one("#calendar-pane").set_class(not self.pref(("notes", "calendar"), True), "-hidden")
        # Inside hill-ops, the preview is a pane of its own, once it has joined.
        shown = self.pref(("notes", "preview"), True) and self.app.side_pane("preview") is None
        self.query_one("#preview-pane").set_class(not shown, "-hidden")
        self.query_one(ActivityCalendar).set_weeks(self.pref(("notes", "weeks"), 16))

    def focused_part(self) -> str | None:
        """Which part of the screen has focus: "list" (the list or the map),
        "calendar" or "preview", the preview's pane beside palace included;
        settings open on its group."""
        if self.app.focus_role in (*ROLES, "claude"):
            return self.app.focus_role
        parts = {"list-pane": "list", "calendar-pane": "calendar", "preview-pane": "preview"}
        for node in self.focused.ancestors_with_self if self.focused else ():
            if node.id in parts:
                return parts[node.id]
        return None

    def set_pref(self, key: tuple[str, ...], value: Any, default: Any) -> None:
        try:
            self.app.prefs.set(key, value, default)
        except (StoreError, OSError) as e:
            self.notify(f"Not saved: {e}", severity="error")
            self.app.report("a setting wasn't saved")

    # -- the vault --

    @property
    def in_map(self) -> bool:
        return self.map_root is not None

    def active_tree(self) -> NotesTree:
        return self.query_one("#map" if self.in_map else "#notes", NotesTree)

    @property
    def current_item(self) -> Any:
        node = self.active_tree().cursor_node
        return node.data if node is not None else None

    @property
    def current(self) -> Note | None:
        item = self.current_item
        if isinstance(item, MapItem):
            return item.note if item.kind in ("note", "cycle") else None
        return item if isinstance(item, Note) else None

    def reload(self, select: str | None = None, quiet: bool = False) -> None:
        """Re-read the vault and redraw everything, keeping the selection.
        A quiet reload, for changes made outside palace, also keeps the
        cursor's folder or map branch and the scroll, and leaves the
        preview alone unless its note changed."""
        before = self.current
        if select is None and before is not None:
            select = before.relpath
        keep = None
        if quiet:
            item = self.current_item
            if select is None and isinstance(item, Folder):
                select = item.path
            if isinstance(item, MapItem):
                keep = self.map_key(item)
        self.vault_stamp = self.vault.stamp() if self.vault.root.is_dir() else None
        self.notes = _work_notes(self.vault.notes()) if self.vault.root.is_dir() else []
        self.projects = self.vault.folders()
        self.graph = self.vault.graph(self.notes)
        self.title_counts = Counter(n.title.casefold() for n in self.notes)
        self.query_one(ActivityCalendar).set_counts(self.vault.activity(self.notes))
        # Show the preview again unless a quiet reload left its note as it was.
        preview = not quiet or before != next((n for n in self.notes if n.relpath == select), None)
        self.build_tree(select, preview=preview)
        if self.map_root is not None:
            root = next((n for n in self.notes if n.relpath == self.map_root.relpath), None)
            self.build_map(root or self.home_note(), select, keep=keep, preview=preview)
        self.update_header()
        self._check_git()
        if self.app.client is not None:
            self.app.send_overview(self.overview())

    @property
    def filtering(self) -> bool:
        """Whether the list shows only some notes: a search, a day or work
        items."""
        return bool(self.search_text) or self.day is not None or self.work_filter is not None

    @property
    def zoomed(self) -> set[str] | None:
        """The projects the list zooms on (`z`); None for the whole list,
        and while filtering, which looks in the whole folder."""
        if self.zoom is None or self.filtering:
            return None
        return {project for group in self.zoom for project in group}

    @property
    def zoom_style(self) -> str:
        """What zooming does (settings: List → Zoom style): "only" shows
        just the zoomed projects, "top" puts them above the rest."""
        return self.pref(("notes", "zoom_style"), "only")

    def long_done(self, note: Note, days: int) -> bool:
        """Whether a work item has been all done or dropped for `days` days
        or more (settings: List → Hide done items), counted from its
        `since:`, else its last commit, else its last change."""
        if _is_sample(note) or self.mark(note) != "done" and note.status != "dropped":
            return False
        since = note.since or self.commit_days.get(note.path) or note.modified.date()
        return (date.today() - since).days >= days

    def visible_notes(self) -> list[Note]:
        notes = self.notes
        if self.search_text:
            needle = self.search_text.casefold()
            notes = [n for n in notes if needle in n.title.casefold()]
        if self.day is not None:
            notes = [n for n in notes if n.modified.date() == self.day]
        if self.work_filter == "progress":
            notes = [n for n in notes if self.mark(n) in FOLDER_MARKS]
        elif self.work_filter is not None:
            notes = [n for n in notes if not _is_sample(n)
                     and self.work_filter in (self.mark(n), n.status, self.scan_mark(n), "decide" if n.decisions else None)]
        days = HIDE_DONE.get(self.pref(("notes", "hide_done"), "never"))
        if days is not None and not self.filtering:
            notes = [n for n in notes if not self.long_done(n, days)]
        zoomed = self.zoomed
        if zoomed is not None and self.zoom_style == "only":
            notes = [n for n in notes if not n.folder or n.folder.split("/")[0] in zoomed]
        if self.pref(("notes", "sort"), "title") == "modified":
            notes = sorted(notes, key=lambda n: n.modified, reverse=True)
        return notes

    def build_tree(self, select: str | None = None, preview: bool = True) -> None:
        """Folders first at each level, like a file browser, then notes.
        Folders closed by hand stay closed, except while filtering, when
        all open so the matches show."""
        tree = self.query_one("#notes", NotesTree)
        scroll = tree.scroll_y
        tree.clear()
        notes = self.visible_notes()
        filtering = self.filtering
        if select and not self.in_map:
            self.reveal(select)
        by_folder: dict[str, list[Note]] = {}
        folders: set[str] = set()
        for note in notes:
            by_folder.setdefault(note.folder, []).append(note)
            folder = note.folder
            while folder:
                folders.add(folder)
                folder = folder.rpartition("/")[0]
        if not filtering:
            # Every project, even one without notes yet (n writes its first).
            folders.update(self.projects)
        # Zoomed (z), only the projects zoomed on, or those first, most
        # recent first, then a separator and the rest.
        zoomed = self.zoomed
        top: list[str] = []
        if zoomed is not None and self.zoom_style == "only":
            folders = {f for f in folders if f.split("/")[0] in zoomed}
        elif zoomed is not None:
            top = [p for group in self.zoom or [] for p in group if p in folders]
        target = None
        # Work items marked by how far they've got, and how many each folder
        # holds of those still in progress, so a closed folder shows it.
        marks = self.mark_styles()
        held: dict[str, Counter[str]] = {}
        for note in notes:
            for mark in (self.mark(note), self.scan_mark(note)):
                if mark in FOLDER_MARKS or mark in SCAN_MARKS:
                    folder = note.folder
                    while folder:
                        held.setdefault(folder, Counter())[mark] += 1
                        folder = folder.rpartition("/")[0]
            folder = note.folder
            while note.decisions and folder and not _is_sample(note):
                held.setdefault(folder, Counter())["decide"] += note.decisions
                folder = folder.rpartition("/")[0]
        # A project's repo ~/projects/sync couldn't fetch or pull, said
        # short: its note up to the colon ("not pulled", "fetch failed").
        stuck: dict[str, list[str]] = {}
        for repo, why in self.sync.get("stuck", []) if self.sync else []:
            stuck.setdefault(repo, []).append(why.partition(":")[0])

        def add(node: TreeNode, folder: str) -> None:
            nonlocal target
            subs = sorted((f for f in folders if f.rpartition("/")[0] == folder), key=str.casefold)
            if not folder and top:
                rest = [f for f in subs if f not in top]
                subs = [*top, ZOOM_SEPARATOR, *rest] if rest or by_folder.get("") else top
            for sub in subs:
                if sub == ZOOM_SEPARATOR:
                    node.add_leaf(Text(ZOOM_SEPARATOR), data=None)
                    continue
                empty = not any(f == sub or f.startswith(sub + "/") for f in by_folder)
                label = Text(sub.rpartition("/")[2] + "/", style="dim" if empty else "bold")
                if empty:
                    label.append("  no notes", style="dim italic")
                for status in FOLDER_MARKS:
                    if count := held.get(sub, Counter())[status]:
                        label.append(f"  ● {count} {status}", style=marks[status])
                for scanned, sign in SCAN_MARKS.items():
                    if count := held.get(sub, Counter())[scanned]:
                        label.append(f"  {sign} {count} {scanned}", style="bold")
                if count := held.get(sub, Counter())["decide"]:
                    label.append(f"  {DECIDE} {count} decide", style=marks["waiting"])
                if sub in stuck:
                    label.append(f"  ⚠ {', '.join(stuck[sub])}", style=marks["waiting"])
                shown = filtering or sub not in self.collapsed
                branch = node.add(label, data=Folder(sub), expand=shown and not empty, allow_expand=not empty)
                if sub == select:
                    target = branch
                add(branch, sub)
            for note in by_folder.get(folder, []):
                label = Text(note.title)
                label.append_text(self.mark_text(note, marks))
                if note.tags:
                    label.append("  " + " ".join(f"#{t}" for t in note.tags), style="dim")
                leaf = node.add_leaf(label, data=note)
                if note.relpath == select:
                    target = leaf

        add(tree.root, "")
        if not notes and (filtering or not self.projects):
            tree.root.add_leaf(Text(self.empty_message(), style="dim"), data=None)
        tree.root.expand()
        self.call_after_refresh(self._move_cursor, target, None if preview else scroll)
        self.update_keys()

    def mark_styles(self) -> dict[str, str]:
        """Each mark's style, in the colour for a dark theme or a light one."""
        dark = self.app.current_theme.dark
        return {mark: f"bold {colours[0] if dark else colours[1]}" for mark, colours in MARKS.items()}

    def mark(self, note: Note) -> str | None:
        """A work item's mark (MARKS): its status, or for one done, how far
        it has got in git; one just done counts as not committed yet. A
        sample has none."""
        if _is_sample(note):
            return None
        if note.status == "done":
            return self.done_states.get(note.path, "to commit")
        return note.status if note.status in MARKS else None

    def scan_mark(self, note: Note) -> str | None:
        """"due" or "dead" (SCAN_MARKS) when ~/projects/scan says the item's
        wait is over, or the job it waits on died; else None."""
        return self.scan.get(note.path.resolve()) if self.scan else None

    def mark_text(self, note: Note, styles: dict[str, str] | None = None) -> Text:
        """A work item's marks, after its title: its status mark (DROPPED
        for one dropped), then ⏳
        or 🤍 when the scan says it's due, or its job died, then ❓N for
        the decisions that wait on you, then ✦ while
        Claude's pane has a session on it (✦ asks, in waiting's colour,
        while Claude asks you something there). A sample's are grey, after
        *sample*: *● sample · waiting ❓1*."""
        text = Text()
        styles = styles or self.mark_styles()
        if _is_sample(note):
            text.append("  ● sample" + (f" · {note.status}" if note.status else "")
                        + (f" {DECIDE}{note.decisions}" if note.decisions else ""), style="dim")
        elif mark := self.mark(note):
            text.append(f"  ● {mark}", style=styles[mark])
        elif note.status == "dropped":
            text.append(f"  {DROPPED} dropped")
        if scanned := self.scan_mark(note):
            text.append(f" {SCAN_MARKS[scanned]}")
        if note.decisions and not _is_sample(note):
            text.append(f" {DECIDE}{note.decisions}", style=styles["waiting"])
        if state := self.app.sessions.get(str(note.path)):
            text.append(" ✦ asks" if state == "asks" else " ✦",
                        style=styles["waiting"] if state == "asks" else "bold" if state == "answering" else "dim")
        elif (key := self.readme_session(note)) is not None:
            text.append_text(self.session_mark(key, styles))
        return text

    def readme_session(self, note: Note) -> str | None:
        """The session on the project whose README `note` is, if any."""
        if not _is_readme(note) or WORK_ITEM.search(note.relpath):
            return None
        return self.app.project_sessions.get(note.path.parent.resolve())

    def session_mark(self, key: str, styles: dict[str, str] | None = None) -> Text:
        """A session's white ✦ (SESSION): dim while idle, bright while
        Claude answers, *✦ asks* in waiting's colour while it asks."""
        state = self.app.sessions.get(key)
        if state == "asks":
            return Text(" ✦ asks", style=(styles or self.mark_styles())["waiting"])
        colour = SESSION[0] if self.app.current_theme.dark else SESSION[1]
        return Text(" ✦", style=f"{'bold' if state == 'answering' else 'dim'} {colour}".strip())

    def line_session(self, node: TreeNode) -> tuple[Text | None, Text | None]:
        """For a line of the list or the map: the white ✦ its label doesn't
        carry, a folder's, and the timer of the session whose mark it
        shows. A project's session marks its README, else its folder
        line, which shows it too while the folder is closed, so it stays
        in sight; a work item's, its own line (✦)."""
        data = node.data.note if isinstance(node.data, MapItem) else node.data
        key, mark = None, None
        if isinstance(data, Note):
            key = str(data.path) if WORK_ITEM.search(data.relpath) and str(data.path) in self.app.sessions \
                else self.readme_session(data)
        elif isinstance(data, Folder) and self.app.project_sessions:
            key = self.app.project_sessions.get((self.vault.root / data.path).resolve())
            readme = any(isinstance(child.data, Note) and _is_readme(child.data) for child in node.children)
            if key is not None and node.is_expanded and readme:
                key = None
            if key is not None:
                mark = self.session_mark(key)
        if key is None:
            return None, None
        since = self.app.session_since.get(key)
        state = self.app.sessions.get(key)
        if since is None or state not in ("idle", "asks"):
            return mark, None
        text, style = self.timer(key, _since_text(time.time() - since))
        return mark, Text(text, style=style)

    def timer(self, key: str, shown: str) -> tuple[str, str]:
        """Session `key`'s timer, `shown`, and its style, by how its prompt
        cache stands (_cache_look): in the mark's style while warm, in
        orange once it goes cold soon (*answer soon*), dim after ❄ once
        cold, since answering then costs the whole conversation again."""
        styles = self.mark_styles()
        look = _cache_look(self.app.session_cold.get(key), time.time())
        if look == "cold":
            return f"{COLD} {shown}", "dim"
        if look == "soon":
            return shown, styles["waiting"]
        return shown, styles["waiting"] if self.app.sessions.get(key) == "asks" else "dim"

    def overview_reason(self, note: Note) -> str | None:
        """Why a work item is in the Overview (a key of OVERVIEW): Claude
        asks something in its session, its job died, its wait is over, it
        waits on you or on a job, or it has a live session; else None."""
        if not WORK_ITEM.search(note.relpath):
            return None
        session = self.app.sessions.get(str(note.path))
        if session == "asks":
            return "asks"
        if scanned := self.scan_mark(note):
            return scanned
        if note.status in ("waiting", "ready", "running"):
            return note.status
        return "live" if session else None

    def overview(self) -> dict:
        """palace's overview for hill-ops's panel: a line per work item that
        wants something (OVERVIEW), and per session on a project, the most
        pressing first, with its marks as in the list; its help says why
        it's there and its `next:`. Zoomed (`z`), it shows only the zoomed
        projects' lines, or puts them first above a separator (List → Zoom
        style); the sync's lines come first either way."""
        styles = self.mark_styles()
        order = list(OVERVIEW)
        listed = [(reason, note.relpath, note.relpath.split("/")[0], note) for note in self.notes
                  if (reason := self.overview_reason(note)) is not None]
        for folder, key in self.app.project_sessions.items():
            try:
                project = folder.relative_to(self.vault.root.resolve()).as_posix()
            except ValueError:
                continue
            project = "" if project == "." else project
            reason = "asks" if self.app.sessions.get(key) == "asks" else "live"
            listed.append((reason, f"{project}/", project.split("/")[0], key))
        listed.sort(key=lambda entry: (order.index(entry[0]), *self.cold_order(entry[3]), entry[1]))
        lines = []
        for reason, _, _, what in listed:
            lines.append(self.overview_line(reason, what, styles) if isinstance(what, Note)
                         else self.project_line(reason, what, styles))
        where = _overview_where("scan" in scripts(self.vault.root))
        if self.zoom is not None:
            zoomed = {project for group in self.zoom for project in group}
            mine = [line for line, entry in zip(lines, listed) if entry[2] in zoomed]
            rest = [line for line, entry in zip(lines, listed) if entry[2] not in zoomed]
            if self.zoom_style == "only":
                # The folder's own session stays, as the notes at its top do.
                lines = mine + [line for line, entry in zip(lines, listed) if entry[2] == ""]
                head, _, tail = where.partition("; ")
                where = f"{head}; zoomed on {len(self.zoom)}" + (f"; {tail}" if tail else "")
            else:
                lines = mine + ([{"separator": True}] if rest else []) + rest
        return {"title": "Overview", "where": where, "verb": "select", "lines": self.sync_lines(styles) + lines,
                "empty": "No work item waits on you, is running or has a session, nor has a project one."}

    def cold_order(self, what: Note | str) -> tuple[int, float]:
        """Where a line of the Overview goes among those of its reason: a
        session still warm, the soonest to go cold first, then the lines
        without a timer, then those gone cold."""
        key = str(what.path) if isinstance(what, Note) else what
        cold = self.app.session_cold.get(key)
        if cold is None or self.app.sessions.get(key) not in ("idle", "asks"):
            return 1, 0.0
        return (0, cold[0]) if cold[0] > time.time() else (2, 0.0)

    def overview_line(self, reason: str, note: Note, styles: dict[str, str]) -> dict:
        """The Overview's line for a work item."""
        mark = self.mark(note)
        project, _, _ = note.relpath.partition("/work/")
        number = WORK_ITEM.search(note.relpath).group(1)
        text = [["● " if mark else "  ", styles[mark] if mark else ""], [f"{project} {number} ", "dim"], [note.title, ""]]
        if scanned := self.scan_mark(note):
            text.append([f" {SCAN_MARKS[scanned]}", ""])
        if note.decisions:
            text.append([f" {DECIDE}{note.decisions}", styles["waiting"]])
        if session := self.app.sessions.get(str(note.path)):
            text.append([" ✦ asks" if session == "asks" else " ✦", styles["waiting"] if session == "asks" else "dim"])
            if session in ("idle", "asks") and (since := self.app.session_since.get(str(note.path))) is not None:
                shown, style = self.timer(str(note.path), _minutes_text(time.time() - since))
                text.append([f" {shown}", style])
        try:
            next_step = str(note.read()[0].get("next") or "")
        except OSError:
            next_step = ""
        help = OVERVIEW[reason][0].upper() + OVERVIEW[reason][1:] + (f". Next: {next_step}" if next_step else ".")
        return {"id": note.relpath, "text": text, "help": help}

    def project_line(self, reason: str, key: str, styles: dict[str, str]) -> dict:
        """The Overview's line for a session on a project: *✦ palace*,
        white, or orange with *asks*, and how long it has been idle or
        asked, in minutes, so that it's sent again only once a minute. A
        pick selects the project's README, else the home note."""
        root = self.vault.root.resolve()
        folder = Path(key).resolve()
        project = "" if folder == root else folder.relative_to(root).as_posix()
        state = self.app.sessions.get(key)
        white = SESSION[0] if self.app.current_theme.dark else SESSION[1]
        colour = styles["waiting"] if state == "asks" else f"{'bold' if state == 'answering' else 'dim'} {white}".strip()
        text = [["✦ ", colour], [project or folder.name, ""]]
        if state == "asks":
            text.append([" asks", styles["waiting"]])
        if state in ("idle", "asks") and (since := self.app.session_since.get(key)) is not None:
            shown, style = self.timer(key, _minutes_text(time.time() - since))
            text.append([f" {shown}", style])
        readme = next((n.relpath for n in self.notes if _is_readme(n) and n.path.parent.resolve() == folder), None)
        home = self.home_note()
        help = ("Claude asks you something in its session on the project." if state == "asks"
                else "Claude has a session on the project.")
        return {"id": readme or (home.relpath if home else ""), "text": text, "help": help}

    def sync_lines(self, styles: dict[str, str]) -> list[dict]:
        """The Overview's first lines, from ~/projects/sync (read_sync): one
        per repo it couldn't fetch or pull, then one when it hasn't run for
        a while. A pick selects the project's README, else the home note."""
        home = self.home_note()
        relpaths = {n.relpath for n in self.notes}

        def note_of(repo: str) -> str:
            readme = f"{repo}/README.md"
            return readme if readme in relpaths else home.relpath if home else ""

        lines = []
        for repo, note in self.sync.get("stuck", []):
            lines.append({"id": note_of(repo), "text": [["⚠ ", styles["waiting"]], [f"{repo} ", "dim"], [note, ""]],
                          "help": f"sync couldn't do this, at {self.sync.get('when', 'its last run')}. "
                                  "Once it's fixed, the timer pulls it within 15 minutes, or :sync now."})
        if late := self.sync.get("late"):
            lines.append({"id": note_of(""), "text": [["⚠ ", styles["waiting"]], ["sync ", "dim"], [f"last ran {late}", ""]],
                          "help": "Whatever runs sync --pull, such as a timer, hasn't for over an hour: "
                                  "has it stopped? Its log says why."})
        return lines

    def wrap_done(self) -> None:
        """Wrap or park the idle sessions whose conversation has handed off,
        as the README's Claude's pane has it (park_reason): its program
        stops, and selecting the item again carries the session on. A
        session parked while shown is offered again in Claude's pane."""
        sessions = self.app.sessions
        self.started_as = {key: self.started_as[key] if key in self.started_as else self.mark_of(key) for key in sessions}
        for key, state in list(sessions.items()):
            if state != "idle" or (reason := self.park_reason(key)) is None:
                continue
            shown = self.app.claude
            self.app.end_claude(key)
            if reason != "done" and shown is not None and shown[0] == key:
                self.app.show_claude(*shown)

    def mark_of(self, key: str) -> str | None:
        """The mark of the item a session's key names, if palace lists it."""
        note = next((n for n in self.notes if str(n.path) == key), None)
        return self.mark(note) if note is not None else None

    def park_reason(self, key: str) -> str | None:
        """Why the idle session `key` parks now, else None: "done" for an
        item done and pushed; its status for one that turned ready or
        running while the session ran (it handed off), once its file is
        written back (committed); and, once idle for Palace → Park idle
        sessions after, its status for one waiting, ready or running
        (written back too), or "project" for a session on a project."""
        note = next((n for n in self.notes if str(n.path) == key), None)
        if note is not None:
            mark = self.mark(note)
            if mark == "done":
                return "done"
            if mark not in ("ready", "running", "waiting"):
                return None
            handed_off = mark != "waiting" and self.started_as.get(key) != mark
            if not handed_off and not self.idle_long(key):
                return None
            return mark if done_states([note.path], self.git_roots).get(note.path) != "to commit" else None
        if WORK_ITEM.search(key) or not Path(key).is_dir():
            return None  # an item palace doesn't list, such as one hidden by a search
        return "project" if self.idle_long(key) else None

    def idle_long(self, key: str) -> bool:
        """Whether session `key` has been idle for Palace → Park idle
        sessions after (never, at 0)."""
        minutes = self.pref(("claude", "park_after"), PARK_AFTER)
        since = self.app.session_since.get(key)
        return bool(minutes) and since is not None and time.time() - since >= minutes * 60

    def find_item(self, spec: str) -> Note | None:
        """The note `spec` names on the command line: its path in the
        vault, with or without .md, or a work item as PROJECT NNN, such as
        "palace 020", as the Overview shows it."""
        spec = spec.strip()
        if m := re.fullmatch(r"(\S+)\s+(\d+)", spec):
            return next((n for n in self.notes if n.relpath.startswith(f"{m[1]}/work/")
                         and (number := WORK_ITEM.search(n.relpath)) and int(number[1]) == int(m[2])), None)
        try:
            relpath = Path(spec).expanduser().resolve().relative_to(self.vault.root.resolve()).as_posix()
        except ValueError:
            relpath = spec
        return next((n for n in self.notes if n.relpath in (relpath, f"{relpath}.md")), None)

    def command_item(self, spec: str, command: str) -> Note | None:
        """The work item a command acts on: the one `spec` names, else the
        one selected; None, having said why, for anything else."""
        note = self.find_item(spec) if spec else self.current
        if note is None:
            self.notify(f":{command} found no note “{spec}”" if spec else f":{command} needs a work item selected",
                        severity="warning")
            self.app.report(f":{command} found no note" if spec else f":{command} needs a work item selected")
        elif not WORK_ITEM.search(note.relpath):
            self.notify(f":{command} is for work items, and {note.title} isn't one", severity="warning")
            self.app.report(f":{command} is for work items")
            return None
        return note

    def set_status(self, arg: str) -> None:
        """`:set-status STATUS [ITEM]`: the item's status, and its `since:`
        today if the status changed."""
        status, _, spec = arg.partition(" ")
        status = status.casefold()
        if status not in ITEM_STATUSES:
            self.notify(f":set-status takes one of {', '.join(ITEM_STATUSES)}", severity="warning")
            return
        if (note := self.command_item(spec.strip(), "set-status")) is None:
            return
        fields: dict[str, Any] = {"status": status}
        if note.status != status:
            fields["since"] = date.today()
        try:
            self.vault.update(note, fields)
        except OSError as e:
            self.notify(f"Can't change {note.title}: {e.strerror}", severity="error")
            self.app.report(f"can't change a work item's status: {e.strerror}")
            return
        self.notify(f"{note.title}: {status}")
        self.reload()

    def select_path(self, path: str, asked: str = "Claude asked", follow: bool = True) -> bool:
        """Select the note at `path` in the list, as Claude asks through
        $PALACE_SELECT, or a pick in the Overview: the preview follows it,
        and Claude's pane too unless not to `follow`. One the list hides,
        outside the vault or not a note, says so instead, `asked` saying who
        asked, and gives False."""
        try:
            relpath = Path(path).resolve().relative_to(self.vault.root.resolve()).as_posix()
        except ValueError:
            relpath = None
        note = next((n for n in self.notes if n.relpath == relpath), None)
        if note is None:
            self.notify(f"{asked} to select a note palace doesn't have: {path}", severity="warning")
            self.app.report(f"{asked} to select a note palace doesn't have")
            return False
        if self.in_map:
            self.action_toggle_map()
        if note not in self.visible_notes():
            self.notify(f"{asked} to select {note.title}, which the list hides now "
                        "(a filter, the zoom, or List → Hide done items)", severity="warning")
            self.app.report(f"{asked} to select a note the list hides")
            return False
        if not follow:
            self.app.claude_held = str(note.path)
        self.build_tree(note.relpath)
        return True

    def reveal(self, relpath: str) -> None:
        """Open the folders around a note that's being selected."""
        folder, opened = relpath.rpartition("/")[0], False
        while folder:
            if folder in self.collapsed:
                self.collapsed.discard(folder)
                opened = True
            folder = folder.rpartition("/")[0]
        if opened:
            self.save_collapsed()

    def save_collapsed(self) -> None:
        self.set_pref(("notes", "collapsed"), sorted(self.collapsed), [])

    def _move_cursor(self, node: TreeNode | None, scroll: float | None = None) -> None:
        """Move the list's cursor to `node`, else the first note, and show
        it; or, given the list's `scroll` from before it was rebuilt, scroll
        back there and leave the preview alone, if `node` is there."""
        tree = self.query_one("#notes", NotesTree)
        self.placed = True
        if scroll is not None and node is not None:
            tree.scroll_to(y=scroll, animate=False, immediate=True)
            _move_to(tree, node)
            return
        if node is None:
            node = self._first_note(tree.root) or next(iter(tree.root.children), None)
        if node is not None:
            _move_to(tree, node)
        self.show_preview(self.current)
        self.keep_place()

    def _first_note(self, node: TreeNode) -> TreeNode | None:
        """The first note on screen: none inside closed folders."""
        for child in node.children:
            if isinstance(child.data, Note):
                return child
            if isinstance(child.data, Folder) and child.is_expanded:
                found = self._first_note(child)
                if found is not None:
                    return found
        return None

    def empty_message(self) -> str:
        if self.day is not None:
            return f"No notes changed on {self.day:%a %d %b}"
        if self.search_text:
            return f"No titles contain “{self.search_text}”"
        if self.work_filter == "progress":
            return "No work items in progress"
        if self.work_filter is not None:
            return f"No work items {self.work_filter}"
        return "No notes yet: press n to write one"

    def update_header(self) -> None:
        count = len(self.notes)
        parts = [f"palace · {_home_relative(self.vault.root)}", f"{count} note{'s' * (count != 1)}"]
        if self.git_line:
            parts.append(self.git_line)
        if self.day is not None:
            parts.append(f"changed on {self.day:%a %d %b}")
        if self.search_text:
            parts.append(f"titles with “{self.search_text}”")
        if self.work_filter == "progress":
            parts.append("work in progress")
        elif self.work_filter is not None:
            parts.append(f"work items {self.work_filter}")
        if self.zoom is not None and self.zoomed is not None and self.zoom_style == "only":
            parts.append(f"zoomed on {len(self.zoom)}")
        if self.map_root is not None:
            parts.append(f"map from {self.map_root.title}{self.where(self.map_root).rstrip('/')}")
        # A restart pending comes early, where the line is never cut off.
        header = Text(parts[0], no_wrap=True, overflow="ellipsis")
        if restart := self.app.restart_text():
            header.append(" · ")
            header.append_text(restart)
        header.append(" · " + " · ".join(parts[1:]))
        self.query_one("#header", Static).update(header)

    def pane_keys(self, keys: list[Key]) -> list[Key]:
        """The keys for a pane's key line: inside hill-ops, without those for the
        whole app, which hill-ops's strip shows."""
        if self.app.client is None:
            return keys
        return [key for key in keys if key.action not in APP_ACTIONS]

    def update_keys(self) -> None:
        filtered = self.filtering
        if self.in_map:
            item = self.current_item
            missing = isinstance(item, MapItem) and item.kind == "missing"
            list_keys = MISSING_KEYS if missing else MAP_KEYS
        else:
            list_keys = [*([CLEAR_FILTER] if filtered else []), *LIST_KEYS]
        self.query_one("#list-keys", Keyline).set_keys(*self.pane_keys(list_keys))
        self.query_one("#calendar-keys", Keyline).set_keys(
            *self.pane_keys([*([CLEAR_FILTER] if self.day is not None else []), *CALENDAR_KEYS])
        )

    # -- the links map --

    def home_note(self) -> Note | None:
        """The vault's home note: one titled Root, Home, Index or Start here,
        the one nearest the top of the vault first (a project's own Root.md
        never beats ~/projects/Home.md), else the note with the most links."""
        homes = [n for n in self.notes if n.title.casefold() in HOME_TITLES]
        if homes:
            return min(homes, key=lambda n: (n.relpath.count("/"), HOME_TITLES.index(n.title.casefold())))
        return max(self.notes, key=lambda n: len(self.graph.links_from(n)), default=None)

    def where(self, note: Note) -> str:
        """The note's folder, when its title alone doesn't say which note it is
        (a README in each project, say)."""
        if self.title_counts[note.title.casefold()] > 1 and note.folder:
            return f"  {note.folder}/"
        return ""

    def map_label(self, note: Note) -> Text:
        label = Text(note.title)
        label.append(self.where(note), style="dim")
        label.append_text(self.mark_text(note))
        count = len(self.graph.links_from(note))
        if count:
            label.append(f"  {count} link{'s' * (count != 1)}", style="dim")
        return label

    def show_map(self, root: Note | None, select: str | None = None) -> None:
        self.query_one("#notes").add_class("-hidden")
        self.query_one("#search").remove_class("-shown")
        tree = self.query_one("#map", MapTree)
        tree.remove_class("-hidden")
        self.build_map(root, select)
        tree.focus()

    def build_map(self, root: Note | None, select: str | None = None,
                  keep: tuple | None = None, preview: bool = True) -> None:
        """The map from `root`: its links, one level further opened, with
        who links to it at the top (and, from the home note, the notes
        nothing links to at the end)."""
        tree = self.query_one("#map", MapTree)
        scroll = tree.scroll_y
        tree.clear()
        self.map_root = root
        if root is None:
            tree.root.set_label(Text("No notes yet: press n to write one", style="dim"))
            tree.root.data = MapItem("group")
            self.update_keys()
            return
        label = Text.assemble((root.title, "bold"), (self.where(root), "dim"))
        label.append_text(self.mark_text(root))
        tree.root.set_label(label)
        tree.root.data = MapItem("note", root, path=(root.relpath,))
        linked_from = self.graph.links_to(root)
        if linked_from:
            count = len(linked_from)
            self._add_group(tree.root, "from", f"↖ linked from {count} note{'s' * (count != 1)}", linked_from, root)
        self.fill(tree.root, open_children=True)
        if root is self.home_note():
            unlinked = self.graph.unlinked(exclude=root)
            if unlinked:
                self._add_group(tree.root, "unlinked", f"not linked from anywhere ({len(unlinked)})", unlinked, root)
        tree.root.expand()
        target = self._find_key(tree.root, keep) if keep else None
        if target is None and select:
            target = self._find(tree.root, select)
        self.call_after_refresh(self._move_map_cursor, target, None if preview else scroll)
        self.update_keys()

    def map_key(self, item: MapItem) -> tuple:
        """What identifies a map branch across rebuilds: its path from the root."""
        return ("group", item.target, *item.path) if item.kind == "group" else item.path

    def _add_group(self, parent: TreeNode, name: str, label: str, notes: list[Note], root: Note) -> None:
        item = MapItem("group", target=name, path=(root.relpath,))
        group = parent.add(Text(label, style="dim"), data=item, expand=self.map_key(item) in self.map_open)
        for note in notes:
            self._add_note(group, note, (root.relpath,))

    def _add_note(self, parent: TreeNode, note: Note, path: tuple[str, ...], open_by_default: bool = False) -> None:
        """A note under `parent`: open if opened by hand before, or by default
        (the root's own links) unless closed by hand."""
        if note.relpath in path:
            parent.add_leaf(Text("↺ " + note.title + self.where(note), style="dim"), data=MapItem("cycle", note, path=path))
            return
        item = MapItem("note", note, path=(*path, note.relpath))
        has_links = bool(self.graph.links_from(note))
        key = self.map_key(item)
        shown = has_links and key not in self.map_closed and (open_by_default or key in self.map_open)
        node = parent.add(self.map_label(note), data=item, allow_expand=has_links, expand=shown)
        if shown:
            self.fill(node)

    def fill(self, node: TreeNode, open_children: bool = False) -> None:
        """Add a note's links under it, the first time it's opened."""
        item = node.data
        if not isinstance(item, MapItem) or item.kind != "note" or item.filled or item.note is None:
            return
        item.filled = True
        for link in self.graph.links_from(item.note):
            if link.note is None:
                label = Text.assemble((link.target, "italic"), ("  no note yet", "dim"))
                node.add_leaf(label, data=MapItem("missing", target=link.target, path=item.path, file=link.path or ""))
            else:
                self._add_note(node, link.note, item.path, open_children)

    def _find(self, node: TreeNode, relpath: str) -> TreeNode | None:
        """The first visible node for a note: none inside closed branches."""
        item = node.data
        if isinstance(item, MapItem) and item.note is not None and item.note.relpath == relpath:
            return node
        if node.is_expanded:
            for child in node.children:
                found = self._find(child, relpath)
                if found is not None:
                    return found
        return None

    def _find_key(self, node: TreeNode, key: tuple) -> TreeNode | None:
        """The visible map node at a branch (`map_key`), if it's still there."""
        if isinstance(node.data, MapItem) and self.map_key(node.data) == key:
            return node
        if node.is_expanded:
            for child in node.children:
                found = self._find_key(child, key)
                if found is not None:
                    return found
        return None

    def _move_map_cursor(self, node: TreeNode | None, scroll: float | None = None) -> None:
        """As _move_cursor, for the map."""
        tree = self.query_one("#map", MapTree)
        self.placed = True
        if scroll is not None and node is not None:
            tree.scroll_to(y=scroll, animate=False, immediate=True)
            _move_to(tree, node)
        else:
            _move_to(tree, node if node is not None else tree.root)
            self.show_preview(self.current)
        self.update_keys()
        self.keep_place()

    def create_missing(self, item: MapItem) -> None:
        """Create the note a link points to (at the linked path, for a
        Markdown link), then open it."""
        folder, _, name = item.file.rpartition("/")
        title = Path(name).stem if item.file else item.target
        try:
            note = self.vault.create(title, folder)
        except OSError as e:
            self.notify(f"Can't create the note: {e.strerror}", severity="error")
            self.app.report(f"can't create a note: {e.strerror}")
            return
        self.reload(select=note.relpath)
        self.app.call_after_refresh(self.open_note, note)

    # -- preview --

    def note_info(self, note: Note | None) -> dict[str, Any]:
        """What the preview shows of a note, as the preview's pane hears
        it: its title, folder (when the title alone doesn't say which note
        it is), when it changed, tags, links each way and body; and its
        path and the folder micro runs in for it, for Claude on the note.
        Nothing for no note."""
        if note is None:
            return {}
        try:
            body = note.body
        except OSError as e:
            body = f"*Can't read this note: {e.strerror}*"
        return {
            "title": note.title, "where": self.where(note), "modified": f"{note.modified:%d %b %Y %H:%M}",
            "tags": list(note.tags),
            "links_to": [l.note.title if l.note else f"{l.target} (no note yet)" for l in self.graph.links_from(note)],
            "linked_from": [n.title for n in self.graph.links_to(note)],
            "body": body, "path": str(note.path), "cwd": str(self.work_dir(note)),
        }

    def show_preview(self, note: Note | None) -> None:
        info = self.note_info(note)
        self.app.tell_sides("note", **info)
        self.app.follow_note(info)
        self.query_one("#preview-title", Static).update(heading(info) if info else "")
        self.query_one("#preview", Markdown).update(str(info.get("body") or ""))
        self.query_one("#preview-scroll", VerticalScroll).scroll_home(animate=False)
        self._check_git()

    def _check_git(self) -> None:
        """Look up, off the screen's thread, how the repository of the note
        or folder under the cursor stands against git, for the header."""
        item = self.current_item
        note = self.current
        if note is not None:
            folder = note.path.parent
        elif isinstance(item, Folder):
            folder = self.vault.root / item.path
        else:
            folder = self.vault.root
        self._git_asked = folder
        self.run_worker(lambda: self._git_checked(folder), thread=True, exclusive=True, group="git", exit_on_error=False)

    def _git_checked(self, folder: Path) -> None:
        root = self.vault.root.resolve()
        repo = repo_root(folder)
        state = git_state(repo or folder)
        if repo is not None and root in repo.resolve().parents:
            state = f"{repo.name}: {state}"
        self.app.call_from_thread(self._show_git, folder, state)

    def _show_git(self, folder: Path, state: str) -> None:
        # A slower answer, for where the cursor was before, comes too late.
        if folder == self._git_asked and state != self.git_line:
            self.git_line = state
            self.update_header()

    # -- events --

    @on(Tree.NodeHighlighted, "#notes")
    def note_highlighted(self, event: Tree.NodeHighlighted) -> None:
        data = event.node.data
        self.show_preview(data if isinstance(data, Note) else None)
        self.keep_place()

    @on(Tree.NodeHighlighted, "#map")
    def map_highlighted(self) -> None:
        self.show_preview(self.current)
        self.update_keys()
        self.keep_place()

    def keep_place(self) -> None:
        """Write where palace is (PLACE), when it moved, so that it starts
        there again, even after a crash."""
        if not self.placed:
            return
        item = self.current_item
        select = item.path if isinstance(item, Folder) else self.current.relpath if self.current else None
        # Where palace starts isn't work of this session, until you move.
        self.moved = self.moved or select != self.start
        project = project_of(select, self.vault.root) if self.moved else None
        if project is not None and self.recent_projects[:1] != [project]:
            self.recent_projects = [project, *(p for p in self.recent_projects if p != project)][:RECENT_PROJECTS]
            _write_label(self.recent_projects[:LABEL_PROJECTS])
        place = {"select": select, "map": self.map_root.relpath if self.map_root else None}
        if place != self.place:
            self.place = place
            _write_place(place)
        session = {"recent": self.recent_projects, "zoom": self.zoom}
        if session != self.session:
            self.session = session
            _write_session(session)

    @on(Tree.NodeExpanded, "#map")
    def map_expanded(self, event: Tree.NodeExpanded) -> None:
        self.fill(event.node)
        if isinstance(event.node.data, MapItem):
            key = self.map_key(event.node.data)
            self.map_open.add(key)
            self.map_closed.discard(key)

    @on(Tree.NodeCollapsed, "#map")
    def map_collapsed(self, event: Tree.NodeCollapsed) -> None:
        if isinstance(event.node.data, MapItem):
            key = self.map_key(event.node.data)
            self.map_closed.add(key)
            self.map_open.discard(key)

    @on(Tree.NodeExpanded, "#notes")
    def folder_opened(self, event: Tree.NodeExpanded) -> None:
        folder = event.node.data
        if isinstance(folder, Folder) and folder.path in self.collapsed:
            self.collapsed.discard(folder.path)
            self.save_collapsed()

    @on(Tree.NodeCollapsed, "#notes")
    def folder_closed(self, event: Tree.NodeCollapsed) -> None:
        folder = event.node.data
        if isinstance(folder, Folder) and folder.path not in self.collapsed:
            self.collapsed.add(folder.path)
            self.save_collapsed()

    @on(ActivityCalendar.DayPicked)
    def day_picked(self, event: ActivityCalendar.DayPicked) -> None:
        self.day = event.day
        self.build_tree()
        self.update_header()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self.search_text = event.value.strip()
        self.build_tree()
        self.update_header()

    @on(Input.Submitted, "#search")
    def search_submitted(self) -> None:
        self.query_one("#notes", NotesTree).focus()

    def on_descendant_focus(self, _event: events.DescendantFocus) -> None:
        self.show_focus()

    def on_descendant_blur(self, _event: events.DescendantBlur) -> None:
        self.show_focus()

    def show_focus(self) -> None:
        """Highlight the key line of the part that has the focus (CSS makes
        its background lighter); none while palace doesn't have it, since
        Textual then takes the focus from its widgets. Where the focus left
        from under the mouse, the mouse rests (hill-client's Hover)."""
        pane = _pane(self.focused)
        if pane is not self.focused_pane:
            if self.focused_pane is not None:
                self.hover.left(self.focused_pane)
            self.focused_pane = pane
        if pane is not None and self.focused is not None:
            self.pane_focus[str(pane.id)] = self.focused
        for line in self.query(Keyline):
            line.set_class(pane is not None and line.parent is pane, "-active")

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """The focus follows the mouse, lazily (hill-client's Hover): into the part
        it moves over, and, inside hill-ops, into palace's pane from the panes
        beside it, unless hill-ops's strip has the focus."""
        pane = _pane(event.widget)
        if not self.hover.moved(event.screen_offset, pane, pane is not None and pane.has_focus_within):
            return
        if not self.app.app_focus and (self.app.client is None or not self.hover.take_focus()):
            return
        self.focus_pane(pane)

    def focus_pane(self, pane: Widget) -> None:
        """Give a part the focus: to what had it last there, if it can still
        take it, else to its tree, calendar or preview."""
        widget = self.pane_focus.get(str(pane.id))
        if widget is None or not widget.is_attached or not widget.display or not widget.focusable:
            widget = {"list-pane": self.active_tree(), "calendar-pane": self.query_one(ActivityCalendar),
                      "preview-pane": self.query_one("#preview-scroll")}[str(pane.id)]
        self.set_focus(widget, scroll_visible=False)

    # -- actions --

    def action_edit(self) -> None:
        item = self.current_item
        if isinstance(item, MapItem) and item.kind == "missing":
            self.create_missing(item)
            return
        if isinstance(item, MapItem) and item.kind == "group":
            self.active_tree().cursor_node.toggle()
            return
        if self.current is not None:
            self.open_note(self.current)

    def open_note(self, note: Note) -> None:
        """Edit `note` in micro, then show what changed, including settings
        changed from micro (Alt-,). Inside hill-ops, micro runs over the panes
        beside palace, which stays as it is meanwhile."""

        def edited() -> None:
            self.app.apply_prefs()
            self.apply_settings()
            self.reload(select=note.relpath)

        self.app.start_work(["micro", str(note.path)], cwd=self.work_dir(note), then=edited)

    def work_dir(self, note: Note) -> Path:
        """Where micro, and so Claude's session, runs for `note`: the git
        repository the note is in, when that's inside the vault (a project
        in ~/projects, say); else its project, the top-level folder it's in;
        else the vault itself."""
        root = self.vault.root.resolve()
        repo = repo_root(note.path.parent)
        if repo is not None and (repo.resolve() == root or root in repo.resolve().parents):
            return repo
        if "/" in note.relpath:
            return self.vault.root / note.relpath.split("/")[0]
        return self.vault.root

    def action_new_note(self, title: str | None = None) -> None:
        """A new note, in the folder under the cursor, named `title`, or
        whatever the answer to "New note title" is."""
        item = self.current_item
        folder = item.path if isinstance(item, Folder) else self.current.folder if self.current is not None else ""

        def create(title: str | None) -> None:
            if not title:
                return
            try:
                note = self.vault.create(title, folder)
            except OSError as e:
                self.notify(f"Can't create the note: {e.strerror}", severity="error")
                self.app.report(f"can't create a note: {e.strerror}")
                return
            self.clear_filters()
            self.reload(select=note.relpath)
            self.app.call_after_refresh(self.open_note, note)

        if title:
            create(title)
        else:
            self.app.ask("New note title" + (f" (in {folder}/)" if folder else ""), "", create)

    def action_rename(self, title: str | None = None) -> None:
        """Rename the selected note to `title`, or to whatever the answer is."""
        note = self.current
        if note is None:
            return

        def rename(title: str | None) -> None:
            if not title or title == note.title:
                return
            try:
                renamed = self.vault.rename(note, title)
            except OSError as e:
                self.notify(f"Can't rename: {e.strerror}", severity="error")
                self.app.report(f"can't rename a note: {e.strerror}")
                return
            self.reload(select=renamed.relpath)

        if title:
            rename(title)
        else:
            self.app.ask(f"Rename “{note.title}” to", note.title, rename)

    def search_for(self, text: str) -> None:
        """Show only notes whose titles contain `text`; all of them for none."""
        if self.in_map:
            self.action_toggle_map()
        search = self.query_one("#search", Input)
        search.value = text
        search.set_class(bool(text), "-shown")
        self.search_text = text.strip()
        self.build_tree()
        self.update_header()
        self.query_one("#notes", NotesTree).focus()

    def help_section(self) -> str:
        """The help section for the part with focus: List, Map, Calendar,
        Preview or Claude."""
        part = self.focused_part() or "list"
        return "Map" if part == "list" and self.in_map else part.capitalize()

    def settings_group(self) -> str | None:
        """The settings group for the part with focus: its own, or, for the
        Claude pane, which has none, hill-ops's Layout, where its width is."""
        part = self.focused_part()
        return "layout" if part == "claude" else part

    def action_search(self) -> None:
        if self.in_map:
            self.action_toggle_map()
        search = self.query_one("#search", Input)
        search.add_class("-shown")
        search.focus()

    def show_work(self, which: str | None = "progress") -> None:
        """Show only work items in progress, or those with a status or mark
        (STATUSES), or all notes again for None."""
        if which is not None and which != "progress" and which not in STATUSES:
            self.notify(f"No status “{which}”: {', '.join(STATUSES)}", severity="error")
            return
        if self.in_map:
            self.action_toggle_map()
        self.work_filter = which
        self.build_tree()
        self.update_header()
        self.query_one("#notes", NotesTree).focus()

    def action_work(self) -> None:
        """Work items in progress, and back to all notes."""
        self.show_work(None if self.work_filter == "progress" else "progress")

    def action_zoom(self) -> None:
        """Zoom the list on the last projects selected in (List → Zoom on),
        and back to the whole list, on the same note."""
        if self.in_map:
            self.action_toggle_map()
        if self.zoom is None:
            zoom = zoom_groups(self.recent_projects, self.projects, self.pref(("notes", "zoom_on"), 3))
            if not zoom:
                self.notify("Nothing to zoom on yet: select a note in a project first", severity="warning")
                return
            self.zoom = zoom
        else:
            self.zoom = None
        self.rebuild()
        self.query_one("#notes", NotesTree).focus()

    def rezoom(self) -> None:
        """Zoom again, on as many projects as List → Zoom on says now."""
        if self.zoom is not None:
            self.zoom = zoom_groups(self.recent_projects, self.projects, self.pref(("notes", "zoom_on"), 3)) or None
        self.rebuild()

    def rebuild(self) -> None:
        """Draw the list again, on the same note or folder."""
        item = self.current_item
        self.build_tree(item.path if isinstance(item, Folder) else self.current.relpath if self.current else None)
        self.update_header()
        if self.app.client is not None:
            self.app.send_overview(self.overview())

    def clear_filters(self) -> None:
        self.search_text = ""
        self.day = None
        self.work_filter = None
        search = self.query_one("#search", Input)
        search.value = ""
        search.remove_class("-shown")
        self.query_one(ActivityCalendar).picked = None

    def action_clear_filter(self) -> None:
        if self.filtering or self.query_one("#search").has_class("-shown"):
            self.clear_filters()
            self.build_tree()
            self.update_header()
            self.query_one("#notes", NotesTree).focus()

    def action_toggle_map(self) -> None:
        note = self.current
        if self.in_map:
            self.map_root = None
            self.query_one("#map").add_class("-hidden")
            self.query_one("#notes").remove_class("-hidden")
            self.build_tree(note.relpath if note else None)
            self.query_one("#notes", NotesTree).focus()
            self.update_header()
            return
        self.clear_filters()
        start = note if note is not None and self.pref(("notes", "map_start"), "home") == "current" else self.home_note()
        self.show_map(start, note.relpath if note else None)
        self.update_header()

    def action_map_here(self) -> None:
        if self.current is not None:
            self.show_map(self.current, self.current.relpath)
            self.update_header()

    def action_map_home(self) -> None:
        self.show_map(self.home_note(), self.current.relpath if self.current else None)
        self.update_header()

    def action_pick_day(self) -> None:
        self.query_one(ActivityCalendar).action_pick()

    def action_toggle_calendar(self) -> None:
        shown = not self.pref(("notes", "calendar"), True)
        self.set_pref(("notes", "calendar"), shown, True)
        self.apply_settings()

    def action_toggle_preview(self) -> None:
        shown = not self.pref(("notes", "preview"), True)
        self.set_pref(("notes", "preview"), shown, True)
        self.apply_settings()
        self.app.send_panes()

    def action_full_preview(self) -> None:
        """The preview at full width, and back; inside hill-ops, its pane takes
        the whole window, and back."""
        if (pane := self.app.side_pane("preview")) is not None:
            self.app.tmux("select-pane", "-t", pane, ";", "resize-pane", "-Z", "-t", pane)
            return
        left = self.query_one("#left")
        full = not left.has_class("-hidden")
        left.set_class(full, "-hidden")
        preview = self.query_one("#preview-pane")
        preview.styles.width = "1fr" if full else "45%"
        if full:
            preview.remove_class("-hidden")
            self.query_one("#preview-scroll").focus()
        else:
            self.apply_settings()
            self.active_tree().focus()


def side_socket() -> Path:
    """Where palace's side panes join it: in hill-ops's folder for the session,
    or else a private folder of palace's own."""
    # Under palace-loop, the loop's: the same after palace restarts, so the
    # side panes join it again and hill-ops leaves them as they are.
    pid = os.environ.get(LOOP) or str(os.getpid())
    if run := os.environ.get("HILL_RUN"):
        return Path(run) / f"palace-{pid}.sock"
    return Path(tempfile.gettempdir()) / f"palace-{os.getuid()}" / f"{pid}.sock"


class PalaceApp(App):
    """The notes screen. Its settings are in hill-ops's strip, under it."""

    BINDINGS = [
        Binding("comma", "settings", "Settings"),
        Binding("question_mark", "help", "Help"),
        Binding("colon", "command", "Command"),
        Binding("space", "tree", "Keys", show=False),
        Binding("q", "quit", "Quit"),
        Binding("alt+c", "claude", "Claude"),
    ]

    def __init__(self, folder: Path | None = None) -> None:
        super().__init__()
        self.prefs = instance_prefs(palace_config_path())
        self.vault = Vault(folder or vault_path())
        docs = str(README) if README.is_file() else None
        commands, help, tree = offered(self.vault.root)
        self.client = Client.from_env("palace", str(PROFILE), APP_KEYS, commands, help, docs, tree)
        """The channel to hill-ops's strip; None outside hill-ops."""
        self.questions: dict[int, Callable[[str | None], None]] = {}
        """What to do with the answer to each question asked on the strip."""
        self.sides: Sides | None = None
        """The panes palace runs beside it inside hill-ops, and talks to."""
        self.focus_role = "palace"
        """Which of palace's panes had the focus last: "palace", or a side
        pane's role ("preview")."""
        self.over: Callable[[], None] | None = None
        """What to do once the program hill-ops runs over the side panes, such
        as micro, has exited; None while none runs."""
        self.code: CodeWatch | None = None
        self.pending = False
        """A change to palace's code leaves it a restart pending."""
        self.sides_pending: set[str] = set()
        """The side panes that said they have a restart pending."""
        self.sessions: dict[str, str] = {}
        """Each session in Claude's pane that runs Claude, by its key (a
        work item's path, else a project's folder): "idle", "answering" or
        "asks", as its hooks say (claude.read_states)."""
        self.session_since: dict[str, float] = {}
        """When each session's state began, for the list's timer."""
        self.session_cold: dict[str, tuple[float, float]] = {}
        """When each session's prompt cache goes cold, and its life
        (claude.cold_at), for the timer's look."""
        self.project_sessions: dict[Path, str] = {}
        """The sessions on a project rather than a work item, by its folder."""
        self.claude: tuple[str, str] | None = None
        """The session Claude's pane shows, and its folder (claude.talk_key)."""
        self.claude_timer: Timer | None = None
        """Until Claude's pane follows the note selected (CLAUDE_AFTER)."""
        self.claude_held: str | None = None
        """The path of the note Claude selected through $PALACE_SELECT:
        Claude's pane doesn't follow it, and stays on the session that
        asked, until another note is selected (follow_note)."""
        self.claude_states: dict[str, dict] = {}
        """How each session stood when palace last read them (check_claude)."""
        self.write_backs: dict[str, float] = {}
        """Each session's last question (its `asked`) that palace has seen
        to, by its key: asked for the write-back, or found it committed."""
        self.overview_sent: dict | None = None
        """The overview hill-ops last heard, for its panel's first tab."""
        self.overview_minute = 0
        """The minute the Overview was last made for its timers (check_claude)."""
        self.restarting: int | None = None
        """RESTART or RESTART_ALL once `:restart` asked for it: palace
        exits so, once it's idle."""

    def get_default_screen(self) -> Screen:
        return NotesScreen()

    def on_text_selected(self, _event: events.TextSelected) -> None:
        copy_selection(self)

    async def on_mount(self) -> None:
        self.apply_prefs()
        self.code = CodeWatch()
        self.set_interval(CHECK_SECONDS, self.check_code)
        if self.client is not None:
            self.client.on("settings.changed", self.settings_changed)
            self.client.on("run", self.run_from_strip)
            self.client.on("command", self.run_command)
            self.client.on("answer", self.answered)
            self.client.on("over.done", self.over_done)
            self.client.on("overview.pick", self.overview_picked)
            self.run_worker(self.client.run(), exit_on_error=False)
            self.sides = Sides(side_socket(), self.side_said, self.sides_changed)
            try:
                await self.sides.start()
            except OSError:
                self.sides = None
                return
            self.tell_sides("theme", name=self.theme)
            for key, entry in read_states().items():
                if not self.claude_runs(entry):
                    forget(key)  # its program has gone, such as with hill-ops
            if (last := shown()) is not None and last[0] in read_states():
                self.claude = last  # hill-ops kept it running: show it again
            self.send_panes()
            self.check_claude()
            self.set_interval(CLAUDE_SECONDS, self.check_claude)

    async def on_unmount(self) -> None:
        if self.sides is not None:
            await self.sides.stop()

    def apply_prefs(self) -> None:
        """Apply palace's app-wide settings, e.g. after they were changed from
        micro while palace waited."""
        theme = self.pref(("theme",), None)
        if theme in self.available_themes and theme != self.theme:
            self.theme = theme
            self.tell_sides("theme", name=theme)

    # -- the panes beside palace, inside hill-ops --

    def send_panes(self) -> None:
        """Ask hill-ops for the panes beside palace: the preview, unless the
        Preview setting hides it, and Claude's pane on the session shown.
        hill-ops keeps the others running out of sight."""
        if self.client is None or self.sides is None:
            return
        view = self.side_spec("preview") if self.pref(("notes", "preview"), True) else None
        claude = None
        if self.claude is not None:
            key = self.claude[0]
            claude = pane_spec(*self.claude, done=self.claude_done(key), mirror=self.claude_mirror(key))
        self.client.send("panes", view=view, claude=claude)

    def claude_mirror(self, key: str) -> str | None:
        """The machine `key`'s project is worked on, when this one keeps it
        as a mirror (read_mirrors), so that its pane offers no session: one
        would leave `session:` lines in its items (work item 042)."""
        path = Path(key)
        return next((host for folder, host in read_mirrors().items() if path == folder or folder in path.parents), None)

    def claude_done(self, key: str) -> bool:
        """Whether `key`'s item is done and pushed (violet), so that its
        pane offers no session: palace would wrap it at once (wrap_done)."""
        if (notes := self.get_screen_notes()) is None:
            return False
        note = next((n for n in notes.notes if str(n.path) == key), None)
        return note is not None and notes.mark(note) == "done"

    def side_spec(self, role: str) -> dict[str, Any]:
        """The program for a side pane, as hill-ops runs it."""
        assert self.sides is not None
        argv = [sys.executable, "-m", "hill", f"_{role}", str(self.sides.path)]
        return {"name": role, "argv": argv, "cwd": str(self.vault.root)}

    # -- Claude's pane (claude.py) --

    def follow_note(self, info: dict[str, Any]) -> None:
        """A note was selected (as note_info gives it): once it stays
        selected a moment, Claude's pane shows its session; not for the
        note Claude selected (claude_held), so that the session that asked
        stays in sight."""
        if self.client is None or self.sides is None or not info:
            return
        if self.claude_timer is not None:
            self.claude_timer.stop()
        if info.get("path") == self.claude_held:
            return
        self.claude_held = None
        key, cwd = talk_key(info), str(info.get("cwd") or "")
        if key and cwd and (key, cwd) != self.claude:
            self.claude_timer = self.set_timer(CLAUDE_AFTER, lambda: self.show_claude(key, cwd))

    def show_claude(self, key: str, cwd: str) -> None:
        """Show `key`'s session in Claude's pane, which runs in `cwd`. The one
        shown before keeps running out of sight, unless Claude never
        started there: then there's nothing to keep."""
        self.claude_timer = None
        before = self.claude
        if before == (key, cwd):
            return
        self.claude = (key, cwd)
        keep_shown(key, cwd)
        self.send_panes()
        if before is not None and before[0] != key and state_of(before[0]).get("state") is None:
            self.end_claude(before[0])

    def end_claude(self, key: str) -> None:
        """Stop `key`'s session's program in Claude's pane, shown or not."""
        if self.client is None:
            return
        self.client.send("panes.end", names=[pane_name(key)])
        forget(key)
        self.claude_states.pop(key, None)  # so that check_claude doesn't take it as ended on its own
        if self.claude is not None and self.claude[0] == key:
            self.claude = None
            keep_shown(None, None)
        if self.sessions.pop(key, None) is not None and (notes := self.get_screen_notes()) is not None:
            notes.reload(quiet=True)

    def claude_runs(self, entry: dict) -> bool:
        """Whether a session's program still runs in the pane it said."""
        pane = entry.get("pane")
        return bool(pane) and self.tmux("display", "-p", "-t", str(pane), "#{pane_pid}") == str(entry.get("pid"))

    def check_claude(self) -> None:
        """Read how Claude's sessions stand, for the list's marks: palace
        wraps or parks those idle whose conversation has handed off
        (NotesScreen.wrap_done), also each minute. A note's path that
        Claude wrote to $PALACE_SELECT selects it, and the preview follows,
        but Claude's pane stays on the session that asked."""
        states, before = read_states(), self.claude_states
        self.claude_states = states
        notes = self.get_screen_notes()
        for key, entry in states.items():
            if (path := selected(key, str(entry.get("cwd") or key))) is not None and notes is not None:
                notes.select_path(path, follow=False)
        if self.claude is not None and self.claude[0] in before and self.claude[0] not in states:
            # The program shown has ended: after Claude, such as by /exit,
            # it's offered again, to carry the session on; else the place stays empty.
            if before[self.claude[0]].get("state") is not None and self.client is not None:
                self.client.send("panes.end", names=[pane_name(self.claude[0])])  # in case hill-ops hasn't seen it end
                self.send_panes()
            else:
                self.claude = None
                keep_shown(None, None)
        sessions = {key: str(entry["state"]) for key, entry in states.items() if entry.get("state")}
        self.session_since = {key: float(entry["since"]) for key, entry in states.items()
                              if key in sessions and isinstance(entry.get("since"), (int, float))}
        self.session_cold = {key: cold for key, entry in states.items()
                             if key in sessions and (cold := cold_at(entry)) is not None}
        if notes is not None and notes.is_mounted:
            for tree in notes.query(NotesTree):
                tree.tick()
            if (minute := int(time.time() // 60)) != self.overview_minute:
                self.overview_minute = minute
                if self.sessions and self.client is not None:
                    self.send_overview(notes.overview())  # its timers
                notes.wrap_done()  # those idle long enough to park
        if sessions != self.sessions:
            self.sessions = sessions
            self.project_sessions = {Path(key).resolve(): key for key in sessions if not WORK_ITEM.search(key)}
            if notes is not None and notes.is_mounted:
                notes.reload(quiet=True)
                notes.wrap_done()
        if notes is not None:
            self.write_back(states, notes)

    def write_back(self, states: dict[str, dict], notes: NotesScreen) -> None:
        """Ask each session on a `doing` item, idle a while since your last
        question, to write the item back (WRITE_BACK, typed in), once per
        question, unless the item was committed since or its pane has the
        focus: you're there, and may be typing. The item's file then holds
        where the work is before the session's cache goes cold."""
        for key, entry in states.items():
            asked = entry.get("asked")
            if not isinstance(asked, (int, float)) or self.write_backs.get(key) == asked:
                continue
            if not wants_write_back(entry, None):
                continue
            note = next((n for n in notes.notes if str(n.path) == key), None)
            if note is None or note.status != "doing" or not (pane := entry.get("pane")):
                continue
            if self.tmux("display", "-p", "-t", str(pane), "#{pane_active}#{window_active}") == "11":
                continue  # the pane in sight has the focus: looked at again next time
            self.write_backs[key] = asked
            if wants_write_back(entry, last_commit_at(Path(key))):
                self.tmux("send-keys", "-t", str(pane), "-l", WRITE_BACK)
                self.tmux("send-keys", "-t", str(pane), "Enter")

    def claude_pane(self) -> str | None:
        """The tmux pane of the session Claude's pane shows, once it has started."""
        return state_of(self.claude[0]).get("pane") if self.claude is not None else None

    # -- restarting (restart.py) --

    def check_code(self) -> None:
        """Mark a restart pending once palace's code has changed."""
        if not self.pending and self.code is not None and self.code.changed():
            self.pending = True
            if (notes := self.get_screen_notes()) is not None:
                notes.update_header()

    def restart(self, everything: bool = False) -> None:
        """`:restart`: restart the panes with a restart pending, each once
        it's idle, palace's own through palace-loop; or, for `everything`,
        hill-ops and all in it."""
        if everything:
            self.restarting = RESTART_ALL
        elif not self.pending and not self.sides_pending:
            self.notify("No restart pending: :restart all restarts everything")
            return
        else:
            self.tell_sides("restart")
            if self.pending:
                self.restarting = RESTART
        self.restart_when_idle()

    def restart_when_idle(self) -> None:
        """Restart, if `:restart` asked for it, once nothing runs over the
        side panes (micro, the shell); the side panes wait for palace to
        come back."""
        if self.restarting is None:
            return
        if self.over is None:
            if self.restarting == RESTART:
                self.tell_sides("restarting")
            self.exit(return_code=self.restarting)
        elif (notes := self.get_screen_notes()) is not None:
            notes.update_header()

    def restart_text(self) -> Text:
        """What palace's top line says of its restart, if one is pending."""
        if self.restarting is not None and self.over is not None:
            return Text("restart once micro or the shell closes", style=PENDING)
        return restart_hint("app.restart_pending") if self.pending else Text()

    def action_restart_pending(self) -> None:
        """A click on *:restart pending* in a top line: `:restart`."""
        self.restart()

    def side_pane(self, role: str) -> str | None:
        """The tmux pane of the side pane in `role`, once it has joined."""
        return self.sides.pane(role) if self.sides is not None else None

    def tell_sides(self, method: str, **params: Any) -> None:
        if self.sides is not None:
            self.sides.send(method, **params)

    def sides_changed(self) -> None:
        """A side pane joined or left: palace's own preview hides or shows,
        and the side panes hear the note selected."""
        if self.sides is not None:
            self.sides_pending &= set(self.sides.joined)  # one restarted says so again
        if (notes := self.get_screen_notes()) is not None and notes.is_mounted:
            notes.apply_settings()
            notes.show_preview(notes.current)

    def side_said(self, role: str, method: str, params: dict) -> None:
        if method == "focus":
            self.focus_role = role
        elif method == "key":
            self.side_key(role, str(params.get("action") or ""))
        elif method == "error":
            self.report(str(params.get("what") or ""))
        elif method == "pending":
            self.sides_pending.add(role)
        elif method == "restart":
            self.restart()

    def side_key(self, role: str, action: str) -> None:
        """A key pressed in a side pane that's palace's to act on."""
        notes = self.get_screen_notes()
        if notes is None:
            return
        self.focus_role = role
        actions = {
            "edit": notes.action_edit,
            "full": notes.action_full_preview,
            "toggle_preview": notes.action_toggle_preview,
            "settings": self.action_settings,
            "help": self.action_help,
            "command": self.action_command,
            "claude": self.action_claude,
            "shell": self.action_shell,
            "quit": self.exit,
            "next_pane": self.action_focus_next,
        }
        if action in actions:
            actions[action]()

    def on_app_focus(self, _event: events.AppFocus) -> None:
        self.focus_role = "palace"

    def action_focus_next(self) -> None:
        """Tab: the next part of palace, then the next pane beside it, and
        round again."""
        chain = self.screen.focus_chain
        last = not chain or self.focused is chain[-1]
        if self.focus_role != "palace" or (self.sides is not None and self.sides.joined and last):
            self.next_pane(self.focus_role)
        else:
            super().action_focus_next()

    def next_pane(self, after: str) -> None:
        """Give the focus to the next of palace's panes after `after`, of
        those in the window; back in palace's own, to the list."""
        order = ["palace", *ROLES, "claude"]
        shown = set(self.tmux("list-panes", "-F", "#{pane_id}").split())
        start = order.index(after) if after in order else 0
        for role in order[start + 1:] + order[:start + 1]:
            pane = (os.environ.get("TMUX_PANE") if role == "palace" else self.claude_pane() if role == "claude"
                    else self.side_pane(role))
            if pane and pane in shown:
                if role == "palace" and (notes := self.get_screen_notes()) is not None:
                    notes.active_tree().focus()
                self.focus_role = role
                self.tmux("select-pane", "-t", pane)
                return

    def tmux(self, *args: str) -> str:
        """A tmux command on hill-ops's tmux, the one palace runs in."""
        try:
            return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout.strip()
        except OSError:
            return ""

    def send_overview(self, overview: dict) -> None:
        """Send hill-ops the overview for its panel, if it changed."""
        if self.client is not None and overview != self.overview_sent:
            self.overview_sent = overview
            self.client.send("overview", **overview)

    def overview_picked(self, params: dict) -> None:
        """A line of the Overview was picked in hill-ops's panel: select its
        item, and the preview and Claude follow it."""
        if (notes := self.get_screen_notes()) is not None:
            notes.select_path(str(self.vault.root / str(params.get("id") or "")), asked="The Overview asked")

    def over_done(self, _params: dict) -> None:
        """The program hill-ops ran over the side panes has exited."""
        then, self.over = self.over, None
        if then is not None:
            then()
        self.restart_when_idle()

    def pref(self, key: tuple[str, ...], default: Any) -> Any:
        try:
            value = self.prefs.get(key)
        except StoreError:
            return default
        return default if value is None else value

    def settings_changed(self, params: dict) -> None:
        """hill-ops changed one of palace's settings: apply it (settings are read
        fresh from the file)."""
        self.apply_prefs()
        screen = self.get_screen_notes()
        if screen is not None:
            screen.apply_settings()
            if params.get("key") in (["notes", "sort"], ["notes", "hide_done"]):
                screen.build_tree()
            elif params.get("key") == ["notes", "zoom_on"]:
                screen.rezoom()
            elif params.get("key") == ["notes", "zoom_style"]:
                screen.rebuild()
        if params.get("key") == ["notes", "preview"]:
            self.send_panes()

    async def run_from_strip(self, params: dict) -> None:
        """A hint in hill-ops's strip was clicked: run it, if it's one of ours."""
        if (action := params.get("action")) in APP_ACTIONS:
            await self.run_action(action)

    def ask(self, question: str, value: str, then: Callable[[str | None], None]) -> None:
        """Ask on hill-ops's strip, or, outside hill-ops, at the bottom of palace;
        `then` gets the answer, or None."""
        if self.client is None:
            self.push_screen(Prompt(question, value), then)
            return
        asked = len(self.questions) + 1
        while asked in self.questions:
            asked += 1
        self.questions[asked] = then
        self.client.send("ask", id=asked, question=question, value=value)

    def answered(self, params: dict) -> None:
        then = self.questions.pop(params.get("id"), None)
        if then is not None:
            then(params.get("value") or None)

    def report(self, what: str) -> None:
        """Tell hill-ops's event log what went wrong, in palace's words, never
        with a note's title or a path; palace shows the error itself."""
        if self.client is not None:
            self.client.send("error", what=what)

    def run_command(self, params: dict) -> None:
        """Run a line from hill-ops's command line: one of COMMANDS."""
        name, _, arg = str(params.get("line") or "").strip().partition(" ")
        arg = arg.strip()
        screen = self.get_screen_notes()
        if screen is None:
            return
        actions = {
            "new": lambda: screen.action_new_note(arg or None),
            "rename": lambda: screen.action_rename(arg or None),
            "edit": screen.action_edit,
            "search": lambda: screen.search_for(arg),
            "status": lambda: screen.show_work(arg.casefold() or "progress"),
            "select": lambda: self.select_item(arg),
            "set-status": lambda: screen.set_status(arg),
            "work": lambda: self.work(arg),
            "sync": lambda: self.run_script("sync", "--pull"),
            "scan": lambda: self.run_script("scan"),
            "map": screen.action_toggle_map,
            "home": screen.action_map_home,
            "here": screen.action_map_here,
            "zoom": screen.action_zoom,
            "calendar": screen.action_toggle_calendar,
            "preview": screen.action_toggle_preview,
            "full": screen.action_full_preview,
            "settings": lambda: self.action_settings(arg or None),
            "restart": lambda: self.restart(everything=arg == "all"),
            "shell": self.action_shell,
            "quit": self.exit,
        }
        if name in actions:
            actions[name]()
        else:
            self.notify(f"palace has no command “{name}”; : lists them, ? explains them", severity="error")

    def select_item(self, spec: str) -> bool:
        """`:select ITEM`: select the note it names (find_item)."""
        notes = self.get_screen_notes()
        if notes is None:
            return False
        note = notes.find_item(spec) if spec.strip() else None
        if note is None:
            self.notify(f":select found no note “{spec}”", severity="warning")
            self.report(":select found no note")
            return False
        return notes.select_path(str(note.path), asked=":select asked")

    def work(self, spec: str) -> None:
        """`:work [ITEM]`: select the item, if named, show its session in
        Claude's pane, and have Claude start on it ("go", typed there) and
        take the focus; one Claude is already busy on, answering or asking
        you something, only shows."""
        notes = self.get_screen_notes()
        if notes is None or (note := notes.command_item(spec.strip(), "work")) is None:
            return
        if spec.strip() and not self.select_item(spec):
            return
        if self.client is None or self.sides is None:
            self.notify(":work needs Claude's pane beside palace, inside hill-ops", severity="warning")
            return
        info = notes.note_info(note)
        key = talk_key(info)
        self.show_claude(key, str(info["cwd"]))
        self.work_in(key, WORK_TRIES)

    def work_in(self, key: str, tries: int) -> None:
        """Type WORK in `key`'s pane, once its program has said which pane it
        runs in, trying again `tries` times."""
        entry = state_of(key)
        if not (pane := entry.get("pane")):
            if tries > 0:
                self.set_timer(WORK_WAIT, lambda: self.work_in(key, tries - 1))
            else:
                self.notify("Claude's pane didn't start", severity="warning")
                self.report("Claude's pane didn't start")
            return
        if entry.get("state") not in ("answering", "asks"):
            self.tmux("send-keys", "-t", str(pane), "-l", WORK)
            self.tmux("send-keys", "-t", str(pane), "Enter")
        self.focus_role = "claude"
        self.tmux("select-pane", "-t", str(pane))

    def run_script(self, name: str, *args: str) -> None:
        """`:sync` or `:scan`: run ~/projects' script of that name, in the
        vault, off the screen; what it says shows once it's done, and the
        notes and the scan's marks are read again."""
        script = self.vault.root / name
        if not os.access(script, os.X_OK):
            self.notify(f"There's no {name} script in {self.vault.root}", severity="warning")
            self.report(f"no {name} script in the folder")
            return
        self.notify(f"Running {name}…")

        def run() -> None:
            try:
                done = subprocess.run([str(script), *args], cwd=self.vault.root, capture_output=True,
                                      text=True, timeout=SCRIPT_SECONDS)
                said, failed = (done.stdout + done.stderr).strip(), done.returncode != 0
            except (OSError, subprocess.SubprocessError) as e:
                said, failed = str(e), True
            self.call_from_thread(self._script_done, name, said, failed)

        self.run_worker(run, thread=True, group=name, exclusive=True, exit_on_error=False)

    def _script_done(self, name: str, said: str, failed: bool) -> None:
        lines = said.splitlines()
        shown = "\n".join(lines[:SCRIPT_LINES] + (["…"] if len(lines) > SCRIPT_LINES else []))
        self.notify(shown or f"{name}: done", title=name, severity="error" if failed else "information",
                    timeout=SCRIPT_NOTICE)
        if failed:
            self.report(f"{name} failed")
        if (notes := self.get_screen_notes()) is not None:
            notes.reload(quiet=True)

    def get_screen_notes(self) -> NotesScreen | None:
        return next((s for s in self.screen_stack if isinstance(s, NotesScreen)), None)

    def action_settings(self, group: str | None = None) -> None:
        """Open the settings on `group`, or else the part that has focus: in
        hill-ops's strip, or outside hill-ops, as `hill-ops settings` on its own."""
        notes = self.get_screen_notes()
        if group is None and notes is not None:
            group = notes.settings_group()
        if self.client is not None:
            self.client.send("settings.open", group=group)
            return
        self.run_program([sys.executable, "-m", "hill_ops", "settings", *([group] if group else [])])
        self.apply_prefs()
        if notes is not None:
            notes.apply_settings()
            notes.build_tree()

    def action_help(self) -> None:
        """palace's help in hill-ops's strip, on the part that has focus."""
        notes = self.get_screen_notes()
        if self.client is None or notes is None:
            self.notify("palace's help is in hill-ops's strip, when palace runs inside hill-ops")
            return
        self.client.send("help.open", section=notes.help_section())

    def action_command(self) -> None:
        """hill-ops's command line, with palace's commands."""
        if self.client is None:
            self.notify("The command line is in hill-ops's strip, when palace runs inside hill-ops")
            return
        self.client.send("command.open")

    def action_tree(self) -> None:
        """hill-ops's key tree, with palace's commands that have no key of their own."""
        if self.client is None:
            self.notify("The key tree is in hill-ops's strip, when palace runs inside hill-ops")
            return
        self.client.send("tree.open")

    def action_claude(self) -> None:
        """hill-ops's panel, on the line to ask Claude about palace, with the
        settings of the part that has focus above it."""
        if self.client is None:
            self.notify("Claude is in hill-ops's strip, when palace runs inside hill-ops")
            return
        notes = self.get_screen_notes()
        self.client.send("claude.open", group=notes.settings_group() if notes is not None else None)

    def action_shell(self) -> None:
        def back() -> None:
            self.apply_prefs()
            screen = self.get_screen_notes()
            if screen is not None:
                screen.apply_settings()
                screen.reload()

        self.start_work([os.environ.get("SHELL") or "/bin/sh"], then=back)

    def start_work(self, argv: list[str], cwd: Path | None = None, then: Callable[[], None] | None = None) -> None:
        """Run micro or the shell, as run_program does; with a restart
        pending, which they would hold back, ask first: Ret restarts
        instead, Esc goes on."""
        if not self.pending or self.restarting is not None:
            self.run_program(argv, cwd, then)
            return

        def answered(answer: str | None) -> None:
            if answer:
                self.restart()
            else:
                self.run_program(argv, cwd, then)

        self.ask("palace has a restart pending: Ret restarts it first, Esc goes on", "restart", answered)

    def run_program(self, argv: list[str], cwd: Path | None = None, then: Callable[[], None] | None = None) -> None:
        """Run a program in `cwd`, or else in the vault, then `then()` once
        it has exited. Inside hill-ops, it runs over the panes beside palace,
        which stays as it is meanwhile, one at a time; outside, it gets the
        terminal until it exits."""
        exe = shutil.which(argv[0])
        if exe is None:
            self.notify(f"{argv[0]} isn't installed", severity="error")
            self.report(f"{Path(argv[0]).name} isn't installed")
            return
        if self.client is not None:
            if self.over is not None:
                self.notify("micro or your shell is open beside palace: quit it first", severity="warning")
                return
            self.over = then or (lambda: None)
            self.client.send("over", name=Path(exe).name, argv=[exe, *argv[1:]], cwd=str(cwd or self.vault.root))
            return
        try:
            with self.suspend():
                subprocess.run([exe, *argv[1:]], cwd=cwd or self.vault.root)
        except SuspendNotSupported:
            self.notify("Can't open other programs from here", severity="error")
            self.report("can't open other programs from here")
            return
        if then is not None:
            then()

