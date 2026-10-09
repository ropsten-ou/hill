"""palace: a notes screen for a Markdown vault, with micro for editing. Its
settings are in hill-ops's strip, under it."""

import os
import sys
from pathlib import Path

USAGE = """\
usage: hill [FOLDER]           the notes screen, for FOLDER, else the folder
                               chosen on the first start
       hill --resume [FOLDER]  pick which of hill-ops's instances to start in
       hill --new [FOLDER]     in a new instance

palace is published as hill, and runs as `hill` or `palace`.

palace starts itself inside hill-ops, whose strip under it holds its settings,
(hill-ops, which it brings along), when it runs in a terminal;
PALACE_NO_HILL=1 keeps it on its own. hill-ops keeps an instance for each
palace, with its place, its panes and their widths, and starts the last one
that isn't running unless told otherwise. `hill-ops settings` shows the
settings on their own.
"""


HILL_OPS = [sys.executable, "-m", "hill_ops"]
"""hill-ops's command, from palace's own environment, where palace's
package brings it: `uv tool install` puts only palace's commands on PATH."""

LOOP = [str(Path(sys.executable).with_name("palace")), "_loop"]
"""palace-loop, as palace starts it inside hill-ops: through its `palace`
command, beside the Python that runs it, so that hill-ops names the
program palace, as its instances and event log did when the import was
palace (work item 018 in hill-ops)."""

AGAIN = "PALACE_HILL_INSTANCE"
"""A file, set by the palace that starts hill-ops, where the palace inside
writes hill-ops's instance, to start hill-ops again in the same one."""


def into_hill(args: list[str], choice: str | None = None) -> None:
    """Start palace again inside hill-ops, under palace-loop, unless it's there
    already, it isn't in a terminal, hill-ops isn't installed, or PALACE_NO_HILL
    is set; and start hill-ops again, in the same instance, each time palace
    asks to restart it all. `choice` ("resume" or "new") says which
    instance to start in."""
    if os.environ.get("HILL_SOCKET"):
        if (note := os.environ.get(AGAIN)) and (instance := os.environ.get("HILL_INSTANCE")):
            try:
                Path(note).write_text(Path(instance).name)
            except OSError:
                pass
        return
    if os.environ.get("PALACE_NO_HILL"):
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    if _hill_installed():
        import subprocess
        import tempfile

        from .restart import RESTART_ALL

        handle, name = tempfile.mkstemp(prefix="palace-instance-")
        os.close(handle)
        env = os.environ | {AGAIN: name}
        flags = [f"--{choice}"] if choice else []
        try:
            while (status := subprocess.call([*HILL_OPS, *flags, "--", *LOOP, *args], env=env)) == RESTART_ALL:
                instance = Path(name).read_text().strip()
                flags = ["--as", instance] if instance else []
        finally:
            Path(name).unlink(missing_ok=True)
        sys.exit(status)


def _hill_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("hill_ops") is not None


def _report_to_hill(errors: list[str]) -> None:
    """Tell hill-ops's event log what went wrong in the sync. It runs once
    palace has left hill-ops's channel, so palace says hello again to do so."""
    import asyncio

    from hill_client import SOCKET, connect, line

    if not (path := os.environ.get(SOCKET)):
        return

    async def tell() -> None:
        _, writer = await connect(path, wait=0)
        writer.write(line("hello", app="palace") + b"".join(line("error", what=what) for what in errors))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    try:
        asyncio.run(tell())
    except OSError:
        pass  # hill-ops has gone: nothing to tell


def main() -> None:
    args = sys.argv[1:]
    if args in (["-h"], ["--help"]):
        print(USAGE, end="")
    elif args[:1] == ["_loop"] and len(args) <= 2:
        # palace-loop, as hill-ops runs it (see restart.py).
        from .restart import loop

        loop(args[1:])
    elif len(args) == 2 and args[0] == "_preview":
        # The preview's pane, beside palace inside hill-ops (see side.py).
        from .side import main as side

        side("preview", args[1])
    elif len(args) >= 5 and args[0] == "_claude":
        # A Claude session's pane, beside palace inside hill-ops (see claude.py).
        from .claude import run_pane

        done = args[4] == "--done"
        mirror = args[5] if args[4] == "--mirror" and len(args) > 5 else None
        sys.exit(run_pane(args[1], args[2], args[3], args[4 + done + 2 * bool(mirror):], done=done, mirror=mirror))
    elif args == ["_hook"]:
        # A Claude Code hook, in a session palace started (see claude.py).
        from .claude import hook

        hook()
    elif len(args) <= 1 or (args[0] in ("--resume", "--new") and len(args) <= 2):
        choice = args[0][2:] if args[:1] in (["--resume"], ["--new"]) else None
        args = args[1:] if choice else args
        folder = Path(args[0]).expanduser() if args else None
        if folder is not None and not folder.is_dir():
            print(f"palace: {folder} isn't a folder", file=sys.stderr)
            sys.exit(2)
        if folder is None:
            from .config import ask_folder

            ask_folder()
        into_hill(args, choice)
        from .app import PalaceApp
        from .restart import RESTART, RESTART_ALL, again
        from .sync import sync_vault

        app = PalaceApp(folder)
        app.run()
        if app.return_code in (RESTART, RESTART_ALL):
            again(app.return_code)  # the sync waits for the last quit
        errors: list[str] = []
        report = sync_vault(
            app.vault.root,
            commit=app.pref(("sync", "commit"), True),
            push=app.pref(("sync", "push"), True),
            failed=errors.append,
        )
        if report:
            print(report)
        if errors:
            _report_to_hill(errors)
    else:
        print(USAGE, end="", file=sys.stderr)
        sys.exit(2)
