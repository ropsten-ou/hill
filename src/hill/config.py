"""Where palace finds its notes, keeps its settings and what it keeps
between runs. The settings themselves are described in hill.toml, for
hill-ops's settings panel."""

from __future__ import annotations

import os
from pathlib import Path

PROFILE = Path(__file__).with_name("hill.toml")
"""palace's settings, described for hill-ops."""
STARTER = Path(__file__).with_name("starter")
"""A few notes to try palace on, copied to ~/notes on a first start that
has no folder to offer."""
README = Path(__file__).parents[2] / "README.md"
"""What palace does, which Claude reads in hill-ops's strip to answer questions
about it. It's there when palace runs from its folder, as it's installed."""


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def palace_config_path() -> Path:
    if env := os.environ.get("PALACE_CONFIG_HOME"):
        return Path(env) / "settings.json"
    return config_home() / "palace" / "settings.json"


def state_home() -> Path:
    """What palace keeps between runs, such as each project's conversation
    with Claude: $PALACE_STATE_HOME, else $XDG_STATE_HOME/palace, else
    ~/.local/state/palace."""
    if env := os.environ.get("PALACE_STATE_HOME"):
        return Path(env)
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "palace"


def vault_path() -> Path:
    """The folder palace shows: $PALACE_VAULT, else the one chosen on the
    first start (saved_folder), else default_folder()."""
    if env := os.environ.get("PALACE_VAULT"):
        return Path(env).expanduser()
    return saved_folder() or default_folder()


def default_folder() -> Path:
    """~/projects where there is one, else the current folder."""
    projects = Path.home() / "projects"
    return projects if projects.is_dir() else Path.cwd()


def saved_folder() -> Path | None:
    """The folder chosen on the first start, `folder` in palace's
    settings.json, or None before that."""
    import json

    try:
        folder = json.loads(palace_config_path().read_text()).get("folder")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(folder).expanduser() if isinstance(folder, str) and folder else None


def _terminal() -> bool:
    import sys

    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_folder() -> None:
    """On the first start in a terminal, without $PALACE_VAULT: ask which
    folder palace shows, and save it as `folder` in palace's
    settings.json, so it isn't asked again. Ret alone takes ~/projects
    where there is one, else ~/notes, made from the starter folder if
    it isn't there."""
    if os.environ.get("PALACE_VAULT") or saved_folder() is not None:
        return
    import sys

    if not _terminal():
        return
    from settings_panel import JsonStore, StoreError

    home = Path.home()
    projects = home / "projects"
    default = projects if projects.is_dir() else home / "notes"
    shown = f"~/{default.relative_to(home)}"
    starter = not default.exists()
    if starter:
        print(f"palace: Ret alone makes {shown}, a few notes to try palace on.")
    while True:
        try:
            answer = input(f"palace: which folder of notes? [{shown}] ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        folder = Path(answer).expanduser().resolve() if answer else default
        if not answer and starter:
            import shutil

            shutil.copytree(STARTER, folder, ignore=shutil.ignore_patterns("__pycache__"))
        if folder.is_dir():
            break
        print(f"palace: {folder} isn't a folder")
    try:
        JsonStore(palace_config_path()).set(
            ("folder",), f"~/{folder.relative_to(home)}" if folder.is_relative_to(home) else str(folder), None)
    except StoreError as e:
        print(f"palace: {e}; it asks again next time", file=sys.stderr)
        os.environ["PALACE_VAULT"] = str(folder)  # this time, and for palace inside hill-ops
