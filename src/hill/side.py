"""The pane palace runs beside it inside hill-ops and talks to: the preview, in
the middle, a program of its own (`palace _preview SOCKET`), which hill-ops
runs in a pane of its own, beside palace's. Claude's pane, on the right,
is the real Claude Code, which palace doesn't talk to this way (see
claude.py).

The focus follows the mouse across it and palace, lazily (hill-client's
Hover), and the pane with the focus has a lighter background (FOCUSED).

It talks to palace over a local socket that palace opens: palace tells
it the note selected and its theme, and it tells palace when it has
the focus and the keys pressed there that are palace's to act on, such as
e to edit the note or `,` for the settings. Messages are JSON-RPC
notifications, one per line, as on hill-ops's channel:

| From | Message | Meaning |
|---|---|---|
| pane | `hello {role, pane}` | I'm the preview, in this tmux pane |
| pane | `focus {}` | I have the focus |
| pane | `key {action}` | This key, palace's to act on, was pressed in me |
| pane | `error {what}` | This went wrong, for hill-ops's event log, in palace's words |
| pane | `pending {}` | My code changed: I have a restart pending |
| pane | `restart {}` | Run `:restart`: its words in my top line were clicked |
| palace | `note {...}` | The note selected (see `note_info`), or none: `{}` |
| palace | `theme {name}` | palace's theme |
| palace | `restart {}` | Restart, if you have a restart pending, once you're idle |
| palace | `restarting {}` | palace restarts: wait for it to come back, don't end |
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Markdown, Static

from keyline import Key, Keyline
from hill_client import Hover, connect, copy, line

from .restart import CHECK_SECONDS, RESTART, CodeWatch, again

ROLES = ("preview",)
"""The panes palace runs beside it and talks to, in their order, left to right."""
SHOWN_AFTER = 0.05
"""Seconds a selected note must stay selected for the preview to show it, so
holding ↓ doesn't render every note on the way."""
FOCUSED = "background: $surface; background-tint: $foreground 5%;"
"""The background of the pane with the focus: lighter than the others' (in a
light theme, darker), as Textual's own focused lists are."""


def _has_focus() -> bool:
    """Whether the tmux pane this runs in has the focus, as far as tmux
    says: it's the active pane of the window shown. tmux tells a pane when
    it gains or loses the focus, but not whether it has it to begin with.
    True outside tmux."""
    pane = os.environ.get("TMUX_PANE")
    if not pane or not os.environ.get("TMUX"):
        return True
    try:
        done = subprocess.run(["tmux", "display", "-p", "-t", pane, "#{pane_active}#{window_active}"],
                              capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return True
    state = done.stdout.strip()
    return state == "11" or not state  # not known: as Textual starts


def copy_selection(app: App) -> None:
    """Text selected with the mouse goes to the clipboard as the button is
    let go, as in a terminal, with no key to press. Textual writes it for
    the terminal (OSC 52), which hill-ops's tmux passes on (set-clipboard),
    and hill-ops's tmux copies it with its copy-command, such as xclip, for
    a terminal that ignores OSC 52 (hill-client's copy)."""
    text = app.screen.get_selected_text()
    if text:
        app.copy_to_clipboard(text)
        copy(text)


def heading(note: dict, after: Text | None = None) -> Text:
    """A note's heading in the preview: its title, folder (when its title
    alone doesn't say which note it is) and when it changed, with `after`
    on the same line, then its tags and what it links to and from. `note`
    is as `note_info` gives it."""
    text = Text(str(note.get("title") or ""), style="bold")
    text.append(str(note.get("where") or ""), style="dim")
    text.append(f"  {note.get('modified') or ''}", style="dim")
    if after is not None:
        text.append_text(after)
    if note.get("tags"):
        text.append("\n" + " ".join(f"#{t}" for t in note["tags"]), style="dim")
    if note.get("links_to"):
        text.append("\nlinks to " + ", ".join(note["links_to"]), style="dim")
    if note.get("linked_from"):
        text.append("\nlinked from " + ", ".join(note["linked_from"]), style="dim")
    return text


class Sides:
    """palace's end: a local socket at `path` that the side panes join.
    `on_message(role, method, params)` hears what they say and
    `on_change()` when one joins or leaves. What palace last said of each
    kind (the note, the theme) goes to a pane as it joins."""

    def __init__(
        self, path: Path, on_message: Callable[[str, str, dict], None], on_change: Callable[[], None],
    ) -> None:
        self.path = path
        self.on_message = on_message
        self.on_change = on_change
        self.server: asyncio.Server | None = None
        self.joined: dict[str, tuple[asyncio.StreamWriter, str]] = {}
        """The panes that have joined: their writer and tmux pane, by role."""
        self.said: dict[str, bytes] = {}

    async def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(self._serve, path=str(self.path))
        os.chmod(self.path, 0o600)

    async def stop(self) -> None:
        for writer, _ in self.joined.values():
            writer.close()
        if self.server is not None:
            self.server.close()
        self.path.unlink(missing_ok=True)

    def pane(self, role: str) -> str | None:
        """The tmux pane of the side pane in `role`, if it has joined."""
        joined = self.joined.get(role)
        return joined[1] if joined else None

    def send(self, method: str, **params: Any) -> None:
        """Tell every side pane something, without waiting."""
        message = line(method, **params)
        self.said[method] = message
        for writer, _ in self.joined.values():
            if not writer.is_closing():
                writer.write(message)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        role = None
        try:
            while raw := await reader.readline():
                try:
                    message = json.loads(raw)
                    method, params = message["method"], message.get("params") or {}
                except (ValueError, KeyError, TypeError):
                    continue
                if method == "hello" and params.get("role") in ROLES:
                    role = params["role"]
                    self.joined[role] = (writer, str(params.get("pane") or ""))
                    for said in self.said.values():
                        writer.write(said)
                    self.on_change()
                elif role is not None:
                    self.on_message(role, method, params)
        except (OSError, ValueError):
            pass  # dropped, or a line too long
        finally:
            if role is not None and self.joined.get(role, (None,))[0] is writer:
                del self.joined[role]
                self.on_change()
            writer.close()


REJOIN_WAIT = 30.0
"""Seconds a side pane waits for palace to come back once it restarts."""
PENDING = "bold #da77f2"
"""How a pane's top line says a restart is pending."""


def restart_hint(action: str) -> Text:
    """*:restart pending*, as a pane's top line says it: the command that
    restarts, and a click on the words runs `action`, which does what
    `:restart` does."""
    return Text(":restart pending", style=Style.parse(PENDING) + Style(meta={"@click": action}))


SIDE_CSS = f"Screen:focus-within {{ {FOCUSED} }}\n"
"""A side pane is lighter while it has the focus."""


class SideApp(App):
    """A side pane's end: a Textual app that joins palace's socket, says
    which pane it is, takes what palace says, and passes palace's keys on
    to it. It ends when palace has gone. The focus follows the mouse into
    it (hill-client's Hover); while it has the focus, its background is lighter (SIDE_CSS)
    and its key line highlighted."""

    ROLE = ""
    BINDINGS = [
        Binding("comma", "key('settings')", show=False),
        Binding("question_mark", "key('help')", show=False),
        Binding("colon", "key('command')", show=False),
        Binding("exclamation_mark", "key('shell')", show=False),
        Binding("q", "key('quit')", show=False),
        Binding("alt+c", "key('claude')", show=False),
        Binding("tab", "key('next_pane')", show=False, priority=True),
        Binding("ctrl+q", "key('quit')", show=False, priority=True),
        Binding("ctrl+r", "restart_now", show=False, priority=True),
    ]
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, socket: str) -> None:
        super().__init__()
        self.socket = socket
        self.writer: asyncio.StreamWriter | None = None
        self.hover = Hover()
        self.focus_back: Widget | None = None
        """Where the focus goes once the pane has it (put_focus)."""
        self.code: CodeWatch | None = None
        self.pending = False
        """A change to the pane's code, or another reason, leaves it a
        restart pending (restart_reason)."""
        self.pending_why = ""
        """Why, for other than its code."""
        self.restarting = False
        """`:restart` asked for it: it restarts once it's idle."""
        self.rejoin = False
        """palace is restarting: wait for it, rather than end."""

    async def on_mount(self) -> None:
        self.run_worker(self.join(), exit_on_error=False)
        # Once its own on_mount has put the focus where it starts.
        self.call_after_refresh(self.check_focus)
        self.code = CodeWatch()
        self.set_interval(CHECK_SECONDS, self.check_code)

    # -- restarting --

    def check_code(self) -> None:
        """Mark a restart pending once the pane needs one (restart_reason)."""
        if self.pending or (reason := self.restart_reason()) is None:
            return
        self.pending = True
        self.pending_why = reason
        self.tell("pending")
        self.show_pending()

    def restart_reason(self) -> str | None:
        """Why the pane needs a restart, in a few words: "" for a change to
        its code, which needs none; None while it needs none."""
        return "" if self.code is not None and self.code.changed() else None

    def show_pending(self) -> None:
        """Show that a restart is pending, and what for, if it waits."""

    def pending_text(self) -> Text:
        """What the pane's top line says of its restart, if one is pending:
        why, unless it's the pane's code, and what it waits for."""
        if not self.pending:
            return Text()
        waits = self.waits_for() if self.restarting else None
        if waits:
            return Text(f"  restart once {waits}", style=PENDING)
        text = Text("  ").append_text(restart_hint("app.restart_all_pending"))
        return text.append(f": {self.pending_why}", style=PENDING) if self.pending_why else text

    def action_restart_all_pending(self) -> None:
        """A click on *:restart pending*: ask palace to run `:restart`."""
        self.tell("restart")

    def waits_for(self) -> str | None:
        """What a restart waits for, such as an answer under way, or None
        when the pane is idle."""
        return None

    def action_restart_now(self) -> None:
        """Ctrl-r: restart this pane, with a restart pending, once it's idle."""
        if not self.pending:
            self.notify("No restart pending in this pane")
            return
        self.restarting = True
        self.restart_when_idle()

    def restart_when_idle(self) -> None:
        """Restart, if `:restart` asked for it, once the pane is idle."""
        if not self.restarting:
            return
        if self.waits_for() is None:
            self.keep_for_restart()
            self.exit(return_code=RESTART)
        else:
            self.show_pending()

    def keep_for_restart(self) -> None:
        """Keep what the pane wants back once it has restarted, in its
        environment, which the restarted pane gets."""

    def check_focus(self) -> None:
        """A side pane starts out of sight, without the focus, and tmux
        doesn't say so until it has had it."""
        if not _has_focus():
            self.app_focus = False

    def watch_app_focus(self, focus: bool) -> None:
        self.query(Keyline).set_class(focus, "-active")
        if focus and self.focus_back is not None:
            self.focus_back.focus()
            self.focus_back = None

    def put_focus(self, widget: Widget) -> None:
        """Focus `widget`, now if the pane has the focus, or else once it
        gets it: a widget focused meanwhile would show the pane as having
        the focus while your keys go elsewhere."""
        if self.app_focus:
            widget.focus()
        else:
            self.focus_back = widget

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """The focus follows the mouse into the pane, lazily (hill-client's Hover)."""
        if self.hover.moved(event.screen_offset, self, self.app_focus) and self.hover.take_focus():
            self.app_focus = True  # tmux says so too, a moment later

    def on_app_blur(self, _event: events.AppBlur) -> None:
        self.hover.left(self)

    async def join(self) -> None:
        """Join palace, say which pane this is, and take what palace says
        until it has gone; join it again if it said it restarts."""
        wait = 5.0
        while True:
            try:
                reader, self.writer = await connect(self.socket, wait=wait)
            except OSError:
                break
            self.rejoin = False
            self.tell("hello", role=self.ROLE, pane=os.environ.get("TMUX_PANE"))
            if self.pending:
                self.tell("pending")
            try:
                while raw := await reader.readline():
                    try:
                        message = json.loads(raw)
                        method, params = message["method"], message.get("params") or {}
                    except (ValueError, KeyError, TypeError):
                        continue
                    self.received(method, params)
            except (OSError, ValueError):
                pass
            if not self.rejoin:
                break
            wait = REJOIN_WAIT
        self.exit()  # palace has gone

    def tell(self, method: str, **params: Any) -> None:
        """Tell palace something, without waiting."""
        if self.writer is not None and not self.writer.is_closing():
            self.writer.write(line(method, **params))

    def received(self, method: str, params: dict) -> None:
        """What palace said: its theme, and restarts, here; the rest is the
        pane's own."""
        if method == "theme" and params.get("name") in self.available_themes:
            self.theme = params["name"]
        elif method == "restart" and self.pending:
            self.restarting = True
            self.restart_when_idle()
        elif method == "restarting":
            self.rejoin = True

    def action_key(self, action: str) -> None:
        """A key that's palace's to act on: e to edit, `,` for the settings."""
        self.tell("key", action=action)

    def on_app_focus(self, _event: events.AppFocus) -> None:
        self.tell("focus")

    def on_text_selected(self, _event: events.TextSelected) -> None:
        copy_selection(self)


PREVIEW_KEYS = [
    Key("↑↓", "scroll"),
    Key("e", "edit", "key('edit')"),
    Key("f", "full", "key('full')"),
    Key("p", "hide", "key('toggle_preview')"),
]


class PreviewApp(SideApp):
    """The preview, in a pane of its own beside palace: the selected note,
    with its tags, what it links to and what links to it."""

    ROLE = "preview"
    CSS = SIDE_CSS + """
    #preview-title { height: auto; padding: 0 1; background: $boost; }
    #preview-scroll { height: 1fr; padding: 0 1; }
    #preview { margin: 0; }
    """
    BINDINGS = [
        *SideApp.BINDINGS,
        Binding("e", "key('edit')", show=False),
        Binding("f", "key('full')", show=False),
        Binding("p", "key('toggle_preview')", show=False),
    ]

    def __init__(self, socket: str) -> None:
        super().__init__(socket)
        self.showing: Any = None
        self.shown: dict = {}
        """The note shown."""

    def compose(self) -> ComposeResult:
        yield Static(id="preview-title")
        with VerticalScroll(id="preview-scroll"):
            yield Markdown(id="preview")
        yield Keyline(*PREVIEW_KEYS, id="preview-keys", classes="-active")

    def on_mount(self) -> None:
        # Textual runs SideApp's on_mount too, which joins palace.
        self.query_one("#preview-scroll").focus()

    def received(self, method: str, params: dict) -> None:
        super().received(method, params)
        if method == "note":
            if self.showing is not None:
                self.showing.stop()
            self.showing = self.set_timer(SHOWN_AFTER, lambda: self.show(params))

    def show(self, note: dict) -> None:
        self.showing = None
        self.shown = note
        self.show_pending()
        self.query_one("#preview", Markdown).update(str(note.get("body") or ""))
        self.query_one("#preview-scroll", VerticalScroll).scroll_home(animate=False)

    def show_pending(self) -> None:
        """The note's heading, and a restart pending after it."""
        pending = self.pending_text()
        title = heading(self.shown, pending) if self.shown else pending
        self.query_one("#preview-title", Static).update(title)


def main(role: str, socket: str) -> None:
    """Run the side pane in `role`, joining palace at `socket`."""
    apps: dict[str, type[SideApp]] = {"preview": PreviewApp}
    app = apps[role](socket)
    app.run()
    if app.return_code == RESTART:
        again(RESTART)
