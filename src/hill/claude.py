"""Claude on the note: the real Claude Code (`claude`), in the pane on the
right of palace's preview, inside hill-ops. Each work item (a project's
`work/NNN-*.md`) has a session of its own, and so does each project for its
other notes. hill-ops runs a program for each (`palace _claude KEY CWD STATE
COMMAND...`, see `pane_spec`), shows the selected note's, and keeps the
others running out of sight, by name (hill-ops's `panes`); `panes.end` stops
one, as palace does once its item is done and pushed.

The program first says which session it carries on: the one the item names
(`session:`, as a runner run or an earlier one left it), or the project's
last. It starts `claude` once you press Ret, with what you typed as your
first question, so selecting a note starts nothing. A new session gets an
id palace picks (`--session-id`), written to the item before `claude`
starts, for `claude --resume`. On an item done and pushed it offers none
(`--done`): palace would wrap it at once. Nor on a mirror's notes
(`--mirror HOST`), a project worked on another machine only: a session here
would leave `session:` lines that sync then drops (work item 042). A work
item's session whose prompt cache has gone cold, and whose context is
big, isn't resumed: it starts a new session on the item's file, which
costs a tenth as much (`cache`, work item 049); `r` resumes it all the
same.

Claude Code's hooks tell palace how each session stands (`hook`): a file
for each, in hill-ops's folder for the session (`_state_dir`), says its tmux pane, its
session, and whether Claude is answering, asks you something, or is idle.
palace reads them for the list's marks, and `:work` types into the pane.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from mdvault import parse, set_fields

from .config import state_home

COMMAND = "claude"
"""Claude Code, found on the PATH; $PALACE_CLAUDE_AGENT names another, such
as the tests' stand-in."""
ITEM = re.compile(r"/work/(\d+)-[^/]+(?<!-sample)\.md$")
"""A work item's note, by its path: it has a session of its own. A sample
(`work/001-x-sample.md`) has its project's, as a plain note does."""
WORK = "go"
"""What palace's `:work` asks Claude on a work item: the item says the
rest (palace's README, What a work item is)."""
SELECT_FILE = "PALACE_SELECT"
"""Set in Claude's environment, to a file: a note's path written there
selects the note in palace's list."""
MOD = Path(__file__).with_name("mod")
"""hill's mod, which each session palace starts loads (`claude
--plugin-dir`): the item above the prompt, a guard on `git add -A` /
`commit -a`, and `/select` (README, Claude on the note)."""
KEY = "PALACE_CLAUDE_KEY"
"""Set in Claude's environment: which session it is (talk_key), for the hooks."""
HOOKS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "Notification", "SessionEnd")
"""The Claude Code hooks palace listens to."""
STATES = {"SessionStart": "idle", "UserPromptSubmit": "answering", "PostToolUse": "answering", "Stop": "idle"}
"""How a session stands after each hook; a Notification that asks you
something (ASKS) makes it "asks"."""
ASKS = ("permission_prompt", "elicitation_dialog")
"""The notifications in which Claude asks you something."""
WRITE_BACK = ("[palace: idle] Write this item back before your cache goes cold: progress and decisions "
              "into its Notes, the next step into next:, what it waits on; commit it, and stop.")
"""What palace types into an idle session on a `doing` item that hasn't
written it back (wants_write_back). Its first words mark it, so that the
hook doesn't take it for a question of yours."""
WRITE_BACK_MARK = "[palace: idle]"
WRITE_BACK_AFTER = 20 * 60
"""How long a session is idle before palace asks for the write-back, in
seconds: well inside the cache's hour, which the turn renews."""
SMALL = 60_000
"""A cold session with fewer tokens in its context than this still
resumes: re-sending it costs about what a new start does."""
RESUME = "r"
"""Typed alone at a cold session's prompt: resume it all the same."""


def command() -> list[str]:
    """Claude's command line: $PALACE_CLAUDE_AGENT, else claude."""
    return shlex.split(os.environ.get("PALACE_CLAUDE_AGENT") or COMMAND)


def talk_key(note: dict[str, Any]) -> str:
    """Which session a note has: a work item's own (its path), else its
    project's (its folder)."""
    path = str(note.get("path") or "")
    return path if ITEM.search(path) else str(note.get("cwd") or "")


def key_name(key: str) -> str:
    """A session's name: its item's project and number, or its project."""
    if m := ITEM.search(key):
        return f"{Path(key).parents[1].name} {m[1]}"
    return Path(key).name or key


def pane_name(key: str) -> str:
    """The name hill-ops knows a session's program by, and shows."""
    return f"Claude on {key_name(key)}"


def pane_spec(key: str, cwd: str, done: bool = False, mirror: str | None = None) -> dict[str, Any]:
    """The program for a session's pane, as hill-ops runs it. Its environment is
    hill-ops's, not palace's, so palace's state folder and Claude's command go
    on its command line; `done` (`--done`) for an item done and pushed,
    `mirror` (`--mirror HOST`) for a project worked on that machine only."""
    flags = ["--done"] if done else ["--mirror", mirror] if mirror else []
    argv = [sys.executable, "-m", "hill", "_claude", key, cwd, str(state_home()), *flags, *command()]
    return {"name": pane_name(key), "argv": argv, "cwd": cwd}


# -- sessions --

SESSION_LINE = re.compile(r"(?m)^session:.*$")


def item_session(path: str) -> str | None:
    """The session a work item names in its frontmatter (`session:`)."""
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    if not text.startswith("---\n") or (end := text.find("\n---", 4)) < 0:
        return None
    m = SESSION_LINE.search(text[:end])
    value = m[0].partition(":")[2].strip().strip("'\"") if m else ""
    return value or None


def _write_item_session(path: str, session: str) -> None:
    """Name `session` in a work item's frontmatter, for the next conversation
    on it and for `claude --resume`; nothing else in the file changes."""
    try:
        text = Path(path).read_text()
    except OSError:
        return
    if not text.startswith("---\n"):
        return
    _write_text(Path(path), set_fields(text, {"session": session}))


def _projects_file() -> Path:
    """Each project's last session, by its folder, for its notes that aren't
    work items. It outlives hill-ops, and every palace shares it, as they share
    the items' `session:`."""
    return state_home() / "claude" / "sessions.json"


def session_of(key: str) -> str | None:
    """The session `key` carries on: the item's `session:`, or the project's last."""
    if ITEM.search(key):
        return item_session(key)
    value = _read_json(_projects_file()).get(key)
    return value if isinstance(value, str) and value else None


def write_session(key: str, session: str) -> None:
    """Keep `session` as the one `key` carries on."""
    if ITEM.search(key):
        _write_item_session(key, session)
        return
    sessions = _read_json(_projects_file())
    sessions[key] = session
    _write_text(_projects_file(), json.dumps(sessions, indent=1))


def transcript(cwd: str, session: str) -> Path:
    """Where Claude Code keeps a session started in `cwd`: its project's
    folder is the path with each character but letters and digits as -."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{session}.jsonl"


def cache(path: Path) -> tuple[float, float, int]:
    """How a session's prompt cache stands, from its transcript's last
    reply: when it was last used, how long it lives (1 hour, or 5 minutes
    when the reply wrote only to the 5-minute cache, as in overage), and the
    tokens in the context then. A transcript with no reply gives its mtime,
    an hour and 0."""
    try:
        with path.open("rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - 1_000_000))
            lines = f.read().decode(errors="replace").splitlines()
        stamp = path.stat().st_mtime
    except OSError:
        return 0.0, 3600.0, 0
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            entry = json.loads(line)
            usage = entry["message"]["usage"]
            at = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        made = usage.get("cache_creation") or {}
        life = 300.0 if made.get("ephemeral_5m_input_tokens") and not made.get("ephemeral_1h_input_tokens") else 3600.0
        tokens = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        return at, life, tokens
    return stamp, 3600.0, 0


_colds: dict[Path, tuple[float, tuple[float, float]]] = {}
"""cold_at's last reading of each transcript, by its mtime."""


def cold_at(entry: dict) -> tuple[float, float] | None:
    """When the session whose state file is `entry` has its prompt cache go
    cold, and how long that cache lives: from its transcript's last reply
    (cache), read again only once the transcript changes; else an hour
    after its state began. None when neither is known."""
    session, cwd = entry.get("session"), entry.get("cwd") or entry.get("key")
    if session and cwd:
        path = transcript(str(cwd), str(session))
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is not None:
            if (seen := _colds.get(path)) is None or seen[0] != mtime:
                at, life, tokens = cache(path)
                _colds[path] = seen = mtime, ((at + life, life) if tokens else (0.0, 0.0))
            if seen[1][1]:
                return seen[1]
    since = entry.get("since")
    return (float(since) + 3600.0, 3600.0) if isinstance(since, (int, float)) else None


def carry_on(key: str, cwd: str) -> str:
    """A new session's first question on a work item whose old session went
    cold: carry it on from its file, with its `next:`."""
    try:
        fields, _ = parse(Path(key).read_text())
    except (OSError, ValueError):
        fields = {}
    path = os.path.relpath(key, cwd)
    step = str(fields.get("next") or "").strip()
    return f"Carry on {path} from its file" + (f"; next: {step}" if step else ".")


# -- how each session stands --

def _state_dir() -> Path:
    """Where the sessions of this hill-ops stand: in hill-ops's folder for the
    session ($HILL_RUN), which ends with hill and its panes, so that two
    palaces, each in its own hill-ops, never take each other's sessions or
    selections; outside hill-ops, in palace's state folder."""
    if run := os.environ.get("HILL_RUN"):
        return Path(run) / "palace-claude"
    return state_home() / "claude"


def state_file(key: str) -> Path:
    """The file that says how `key`'s session stands."""
    return _state_dir() / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"


def select_file(key: str) -> Path:
    """$PALACE_SELECT for `key`'s session."""
    return state_file(key).with_suffix(".select")


def read_states() -> dict[str, dict]:
    """How each session stands, by its key: its tmux `pane` and the `pid`
    running there, its `cwd`, its `session` and its `state` ("answering",
    "asks", "idle", or None until `claude` starts), and `since`, when that
    state began (`at` is the last event's); `asked`, when you last asked
    it something, and `wrote_back`, when palace last asked for the
    write-back (WRITE_BACK)."""
    states = {}
    for path in sorted(_state_dir().glob("*.json")):
        entry = _read_json(path)
        if isinstance(entry.get("key"), str) and path == state_file(entry["key"]):
            states[entry["key"]] = entry
    return states


def state_of(key: str) -> dict:
    """How `key`'s session stands, as read_states gives each; {} for none."""
    return _read_json(state_file(key))


def shown() -> tuple[str, str] | None:
    """The session palace last showed in Claude's pane, and its folder, so
    that palace shows it again after a restart, which hill-ops kept running."""
    entry = _read_json(_state_dir() / "shown.json")
    key, cwd = entry.get("key"), entry.get("cwd")
    return (key, cwd) if isinstance(key, str) and isinstance(cwd, str) else None


def keep_shown(key: str | None, cwd: str | None) -> None:
    """Keep the session shown in Claude's pane, or none."""
    path = _state_dir() / "shown.json"
    if key is None or cwd is None:
        path.unlink(missing_ok=True)
    else:
        _write_text(path, json.dumps({"key": key, "cwd": cwd}))


def forget(key: str) -> None:
    """Forget how `key`'s session stood: it was stopped."""
    state_file(key).unlink(missing_ok=True)
    select_file(key).unlink(missing_ok=True)


def selected(key: str, cwd: str) -> str | None:
    """A note's path that `key`'s Claude wrote to $PALACE_SELECT, once:
    relative, it's from `cwd`, the project Claude runs in."""
    path = select_file(key)
    try:
        lines = path.read_text().strip().splitlines()
    except OSError:
        return None
    path.unlink(missing_ok=True)
    if not lines or not lines[0].strip():
        return None
    return str(Path(cwd) / Path(lines[0].strip()).expanduser())


def hook() -> None:
    """`palace _hook`, as Claude Code runs it for each of HOOKS, with the
    event on stdin: keep how the session stands in its file. Only in a
    session palace started ($PALACE_CLAUDE_KEY), and silent, since what a
    SessionStart hook prints goes to Claude."""
    key = os.environ.get(KEY)
    if not key:
        return
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(event, dict):
        return
    name = event.get("hook_event_name")
    if name == "SessionEnd":
        forget(key)
        return
    path = state_file(key)
    entry = _read_json(path) or {"key": key}
    entry["pane"] = os.environ.get("TMUX_PANE") or entry.get("pane")
    session = event.get("session_id")
    if isinstance(session, str) and session:
        if name == "SessionStart" and session != session_of(key):
            write_session(key, session)  # such as after /clear
        entry["session"] = session
    state = STATES.get(str(name))
    if name == "Notification" and event.get("notification_type") in ASKS:
        state = "asks"
    if name == "UserPromptSubmit":
        prompt = event.get("prompt")
        mine = isinstance(prompt, str) and prompt.startswith(WRITE_BACK_MARK)
        entry["wrote_back" if mine else "asked"] = time.time()
    if state is not None and state != entry.get("state"):
        entry["since"] = time.time()  # when the state began, for the list's timer
    if state is not None:
        entry["state"] = state
    entry["at"] = time.time()
    _write_text(path, json.dumps(entry))


def wants_write_back(entry: dict, committed: float | None, now: float | None = None) -> bool:
    """Whether a session on a `doing` item wants palace to ask for the
    write-back, as read_states gives its `entry`: idle WRITE_BACK_AFTER
    since your last question, not asked for one since, and its item not
    committed since (`committed`, the time of its last commit, or None)."""
    now = time.time() if now is None else now
    asked, since = entry.get("asked"), entry.get("since")
    if entry.get("state") != "idle" or not isinstance(asked, (int, float)) or not isinstance(since, (int, float)):
        return False
    if now - since < WRITE_BACK_AFTER:
        return False
    wrote_back = entry.get("wrote_back")
    if isinstance(wrote_back, (int, float)) and wrote_back >= asked:
        return False
    return committed is None or committed < asked


def _settings() -> Path:
    """The settings palace gives Claude (`claude --settings`), over your
    own: its hooks, and the fullscreen mode, in which Claude watches the
    mouse, so that hill-ops gives its pane the focus as the mouse rests there."""
    hook_command = f"{shlex.quote(sys.executable)} -m hill _hook"
    settings = {"hooks": {name: [{"hooks": [{"type": "command", "command": hook_command}]}] for name in HOOKS},
                "tui": "fullscreen"}
    path = _state_dir() / "settings.json"
    _write_text(path, json.dumps(settings, indent=1))
    return path


# -- the program in the pane --

def run_pane(key: str, cwd: str, state: str, claude: list[str], done: bool = False, mirror: str | None = None) -> int:
    """`palace _claude KEY CWD STATE [--done | --mirror HOST] COMMAND...`, as
    hill-ops runs it: say which session it carries on, then, once you press
    Ret, become `claude` on it, with what you typed as your first question.
    On an item done and pushed (`done`) it starts nothing, since palace would
    wrap the session at once: it says how to carry the item on, until Ctrl-d.
    Nor on a mirror's notes (`mirror`, the machine its project is worked on):
    it says where to work on it instead."""
    os.environ["PALACE_STATE_HOME"] = state
    os.environ[KEY] = key
    os.environ[SELECT_FILE] = str(select_file(key))
    pane = os.environ.get("TMUX_PANE")
    entry = {"key": key, "cwd": cwd, "pane": pane, "pid": _pane_pid(pane), "state": None, "at": time.time()}
    _write_text(state_file(key), json.dumps(entry))  # palace knows its pane before Claude starts
    session = session_of(key)
    resume = session is not None and transcript(cwd, session).exists()
    print(f"\033[1mClaude on {key_name(key)}\033[0m  \033[2m{_home(cwd)}\033[0m\n")
    if done or mirror:
        if done:
            print("It's done and pushed, so palace wraps its session.")
            print("To carry it on, reopen it (status: doing); for something new, file an item.")
        else:
            print(f"Its project is worked on {mirror}: this copy is a mirror, kept in step by sync,")
            print(f"so palace starts no session here. To work on it, ssh {mirror}.")
        print("\033[2mCtrl-d closes.\033[0m\n")
        try:
            while True:
                input()
        except (EOFError, KeyboardInterrupt):
            forget(key)
            return 0
    cold = False
    if resume:
        assert session is not None
        at, life, tokens = cache(transcript(cwd, session))
        size = f"{round(tokens / 1000)}k tokens"
        if time.time() < at + life:
            print(f"It carries on session {session[:8]}, of {_when(at)}: its cache is warm until {_when(at + life)}.")
        elif tokens < SMALL or not ITEM.search(key):
            print(f"It carries on session {session[:8]}, of {_when(at)}: cold since {_when(at + life)}, {size}.")
        else:
            cold = True
            print(f"Session {session[:8]} went cold at {_when(at + life)}, with {size}: "
                  "it starts a new session on the item's file.")
    else:
        print("It starts a new session.")
    if cold:
        print(f"\033[2mType a question and Ret, or Ret alone; {RESUME} and Ret resumes {session[:8]} instead; "
              "Ctrl-d closes.\033[0m\n")
    else:
        print("\033[2mType a question and Ret, or Ret alone; Ctrl-d closes.\033[0m\n")
    try:
        first = input("✦ ").strip()
    except (EOFError, KeyboardInterrupt):
        forget(key)
        return 0
    if cold:
        if first == RESUME:
            first = ""
        else:
            resume = False
            first = f"{carry_on(key, cwd)}\n\n{first}" if first else carry_on(key, cwd)
    if not resume:
        session = str(uuid.uuid4())
        write_session(key, session)
    assert session is not None
    argv = [*claude, "--settings", str(_settings()), "--plugin-dir", str(MOD),
            *(["--resume", session] if resume else ["--session-id", session]), *([first] if first else [])]
    try:
        os.chdir(cwd)
        os.execvp(argv[0], argv)
    except OSError as e:
        forget(key)
        print(f"Can't start {argv[0]}: {e.strerror}", file=sys.stderr)
        return 1
    return 0


def _pane_pid(pane: str | None) -> int:
    """The pid tmux gives `pane`, which palace checks to know the program
    still runs there: hill-ops's relay, which runs this program beside palace;
    this program's own outside tmux, or if tmux doesn't say."""
    if pane:
        try:
            done = subprocess.run(["tmux", "display", "-p", "-t", pane, "#{pane_pid}"],
                                  capture_output=True, text=True, timeout=2)
            if done.stdout.strip().isdigit():
                return int(done.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return os.getpid()


def _home(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _when(stamp: float) -> str:
    """When, as 16:03 today, else as Tue 30 Sep 16:03."""
    at = datetime.fromtimestamp(stamp)
    return f"{at:%H:%M} today" if at.date() == datetime.now().date() else f"{at:%a %d %b %H:%M}"


# -- files --

def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_text(path: Path, text: str) -> None:
    """Write a file whole, so a reader never sees half of it."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = path.with_name(f".{path.name}.{os.getpid()}")
        new.write_text(text)
        new.replace(path)
    except OSError:
        pass
