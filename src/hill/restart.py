"""Restarting palace's panes after a change to their code, each on its own
where it can, so the rest keeps running.

Each pane palace runs (palace's list, the preview) is a process of its
own and watches its own code (CodeWatch): the files of the modules it has
loaded that you may change, palace's and the packages installed editable,
not the standard library or installed packages. A pane whose code changed
has a restart pending, until `:restart` restarts it once it's idle.
Claude's pane is Claude Code, which restarts as you quit and resume it.

Inside hill-ops, palace runs under palace-loop (`loop`), which runs it again
when it exits with RESTART, so hill-ops, its strip and the side panes stay; a
side pane restarts itself in its own pane. RESTART_ALL ends the loop, and
hill-ops with it, for the `palace` command to start hill-ops again: for a change
to hill-ops's own code, which only that picks up.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

RESTART = 75
"""palace's exit status to be run again by palace-loop."""
RESTART_ALL = 76
"""palace's exit status to have hill-ops, and everything in it, started again."""
LOOP = "PALACE_LOOP"
"""Set while palace runs under palace-loop, to the loop's process id."""
CHECK_SECONDS = 2.0
"""How often a pane looks whether its code changed."""


def own_files() -> dict[Path, float]:
    """The source files of the modules loaded in this process that are code
    you may change, with their modification times: not the standard
    library's, nor an installed package's (in site-packages)."""
    installed = {Path(p).resolve() for p in (sys.base_prefix, sys.prefix, sys.exec_prefix)}
    files: dict[Path, float] = {}
    for module in list(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if not name or not name.endswith(".py"):
            continue
        path = Path(name).resolve()
        if "site-packages" in path.parts or any(path.is_relative_to(p) for p in installed):
            continue
        try:
            files[path] = path.stat().st_mtime
        except OSError:
            pass
    return files


class CodeWatch:
    """A pane's code as it started (own_files), to tell when it changed."""

    def __init__(self) -> None:
        self.files = own_files()

    def changed(self) -> bool:
        """Whether one of the files changed or went since the pane started."""
        for path, mtime in self.files.items():
            try:
                if path.stat().st_mtime != mtime:
                    return True
            except OSError:
                return True
        return False


def again(status: int) -> None:
    """After palace or a side pane exited to restart: under palace-loop,
    exit for it to run palace again; otherwise run the same command again,
    in this process."""
    if os.environ.get(LOOP) and status in (RESTART, RESTART_ALL):
        sys.exit(status)
    os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])


def loop(args: list[str] | None = None) -> None:
    """`palace-loop [FOLDER]` (or `palace _loop [FOLDER]`, as hill-ops runs it):
    run palace, and again each time it exits with RESTART; any other status
    ends the loop with that status. Ctrl-C is palace's, not the loop's."""
    for signum in (signal.SIGINT, signal.SIGQUIT):
        signal.signal(signum, lambda *_: None)
    env = {**os.environ, LOOP: str(os.getpid())}
    argv = [sys.executable, "-m", "hill", *(sys.argv[1:] if args is None else args)]
    while (status := subprocess.call(argv, env=env)) == RESTART:
        pass
    sys.exit(status)
