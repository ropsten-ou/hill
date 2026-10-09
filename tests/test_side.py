"""palace's side pane on its own: the preview, joined to a socket of the
test's own, as palace's would be."""

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest
from textual import events

import hill_client
from keyline import Keyline
from hill.restart import RESTART
from hill.side import PreviewApp, Sides


@pytest.fixture
def socket():
    folder = Path(tempfile.mkdtemp(prefix="ps", dir="/tmp"))  # socket paths must be short
    yield folder / "s.sock"
    shutil.rmtree(folder)


async def until(condition, what="condition"):
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def run_side(app_class, socket, script, size=(60, 24)):
    """Run a side pane joined to a Sides of the test's own; `script(app,
    pilot, sides, said)` drives it, `said` holding what the pane told palace."""
    async def go():
        said = []
        sides = Sides(socket, lambda role, method, params: said.append((role, method, params)), lambda: None)
        await sides.start()
        app = app_class(str(socket))
        try:
            async with app.run_test(size=size) as pilot:
                await until(lambda: sides.joined, "the pane joined")
                return await script(app, pilot, sides, said)
        finally:
            await sides.stop()

    return asyncio.run(go())


NOTE = {"title": "garden", "where": "", "modified": "01 Oct 2026 10:00", "tags": ["project"], "links_to": ["Projects"],
        "linked_from": [], "body": "# garden\n\nListed in [[Projects]].\n", "path": "/v/projects/garden.md", "cwd": "/v/projects"}


def test_the_preview_shows_the_note_palace_selects_and_passes_its_keys_back(socket):
    async def script(app, pilot, sides, said):
        sides.send("note", **NOTE)
        await until(lambda: "Listed in" in app.query_one("#preview").source, "the note")
        title = str(app.query_one("#preview-title").render())
        await pilot.press("e", "f", "p", "comma", "tab")
        await until(lambda: len([s for s in said if s[1] == "key"]) == 5, "the keys")
        return title, [p["action"] for _, method, p in said if method == "key"], sides.pane("preview")

    title, keys, pane = run_side(PreviewApp, socket, script)
    assert title.startswith("garden") and "#project" in title and "links to Projects" in title
    assert keys == ["edit", "full", "toggle_preview", "settings", "next_pane"]


def background(app):
    return app.screen.background_colors[1].hex


def test_a_side_pane_takes_the_focus_as_the_mouse_moves_over_it_and_shows_it(socket, monkeypatch):
    hill_says = []
    monkeypatch.setattr(hill_client, "take_focus", lambda: hill_says.pop(0))
    monkeypatch.setattr(hill_client.Hover, "RETRY", 0)

    async def script(app, pilot, sides, said):
        line = app.query_one(Keyline)
        seen = [(background(app), line.has_class("-active"))]
        app.post_message(events.AppBlur())  # the focus went to another pane
        await pilot.pause()
        seen.append((background(app), line.has_class("-active")))
        hill_says.extend([False, True])
        await pilot.hover("#preview-scroll", offset=(5, 5))  # rests here: the focus left from under it
        await pilot.hover("#preview-scroll", offset=(5, 9))  # hill-ops says no: its strip has the focus
        seen.append(app.app_focus)
        await pilot.hover("#preview-scroll", offset=(5, 12))
        await pilot.pause()
        seen.append((background(app), line.has_class("-active"), app.focused.id))
        return seen

    seen = run_side(PreviewApp, socket, script)
    assert seen == [("#272727", True), ("#121212", False), False, ("#272727", True, "preview-scroll")]


def test_a_side_pane_puts_the_focus_only_where_it_has_it(socket):
    async def script(app, pilot, sides, said):
        app.post_message(events.AppBlur())
        await pilot.pause()
        app.put_focus(app.query_one("#preview-scroll"))  # such as Claude asking before a tool
        await pilot.pause()
        seen = [app.focused, background(app)]
        app.post_message(events.AppFocus())
        await pilot.pause()
        return [*seen, app.focused.id, background(app)]

    assert run_side(PreviewApp, socket, script) == [None, "#121212", "preview-scroll", "#272727"]


async def drag(pilot, selector, start, end):
    """Select with the mouse in `selector`, from one offset to another."""
    await pilot.mouse_down(selector, offset=start)
    await pilot.hover(selector, offset=end)
    await pilot.mouse_up(selector, offset=end)
    await pilot.pause()


def test_text_selected_with_the_mouse_goes_to_the_clipboard(socket):
    async def preview(app, pilot, sides, said):
        sides.send("note", **NOTE)
        await until(lambda: "Listed in" in app.query_one("#preview").source, "the note")
        await drag(pilot, "#preview", (0, 0), (30, 6))
        return app._clipboard

    assert "Listed in" in run_side(PreviewApp, socket, preview)


def changed_code(app):
    """The pane's code changed, as its look every few seconds would find."""
    app.code.files = {Path("/nonexistent/side.py"): 0.0}
    app.check_code()


def test_a_side_pane_with_a_restart_pending_says_so_and_restarts_when_palace_asks(socket):
    async def script(app, pilot, sides, said):
        sides.send("note", **NOTE)
        await until(lambda: "Listed in" in app.query_one("#preview").source, "the note")
        changed_code(app)
        title = str(app.query_one("#preview-title").render())
        sides.send("restart")
        await until(lambda: app.return_code is not None, "the pane restarting")
        return title, [method for _, method, _ in said], app.return_code

    title, said, status = run_side(PreviewApp, socket, script)
    assert title.splitlines()[0] == "garden  01 Oct 2026 10:00  :restart pending"
    assert "pending" in said and status == RESTART


def test_a_click_on_restart_pending_asks_palace_to_restart(socket):
    async def script(app, pilot, sides, said):
        sides.send("note", **NOTE)
        await until(lambda: "Listed in" in app.query_one("#preview").source, "the note")
        changed_code(app)
        title = app.query_one("#preview-title")
        at = str(title.render()).index(":restart pending")
        await pilot.click(title, offset=(1 + at + 3, 0))  # its left padding, then into the words
        await until(lambda: any(method == "restart" for _, method, _ in said), "the pane asking palace")
        return app.is_running

    assert run_side(PreviewApp, socket, script)  # palace restarts it, as for :restart


def test_a_side_pane_waits_for_palace_to_come_back_once_it_restarts(socket):
    async def script(app, pilot, sides, said):
        sides.send("restarting")
        await until(lambda: app.rejoin, "palace saying it restarts")
        await sides.stop()
        back = Sides(socket, lambda role, method, params: said.append((role, method, params)), lambda: None)
        await back.start()
        try:
            await until(lambda: back.joined, "the pane joined again")
            return app.is_running
        finally:
            await back.stop()

    assert run_side(PreviewApp, socket, script)


