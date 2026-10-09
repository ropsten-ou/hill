"""palace's design docs are its READMEs: README.md says what palace does, and
each package's README what it offers; docs/reference.md lists every key,
command and setting, generated (tests/test_reference.py). These tests fail
when something new isn't in them yet: a bound key not in palace's help, and
so not in the reference, a setting the README names that doesn't exist, a
package's API its README misses. They only check names, so a change to what
something does still needs its README changed by hand; see CLAUDE.md."""

import inspect
import re
from dataclasses import is_dataclass
from pathlib import Path

import pytest
from textual.widget import Widget

import mdvault
import hill.app
import hill.calendar
import hill.claude
import hill.side
from hill.config import PROFILE
from hill_ops.profiles import load

README = (Path(__file__).parents[1] / "README.md").read_text()

# How the docs write the keys Textual names; palace's help has them without backquotes.
KEYS = {
    "enter": "Ret", "escape": "Esc", "tab": "Tab",
    "left": "←", "right": "→", "up": "↑", "down": "↓",
    "slash": "`/`", "comma": "`,`", "exclamation_mark": "`!`",
    "question_mark": "`?`", "colon": "`:`", "alt+c": "`Alt-c`", "ctrl+q": "`Ctrl-q`",
    "ctrl+n": "`Ctrl-n`", "ctrl+r": "`Ctrl-r`",
    "pageup": "PgUp", "pagedown": "PgDn", "space": "Space",
}


def bound_keys() -> set[str]:
    """Every key bound in palace's own screens and widgets."""
    return {
        key
        for module in (hill.app, hill.calendar, hill.side, hill.claude)
        for cls in vars(module).values()
        if inspect.isclass(cls) and cls.__module__ == module.__name__ and "BINDINGS" in vars(cls)
        for binding in cls.BINDINGS
        for key in binding.key.split(",")
    }


@pytest.fixture
def groups(tmp_path, monkeypatch):
    """palace's settings groups, read from its profile the way hill-ops reads it."""
    monkeypatch.setenv("PALACE_CONFIG_HOME", str(tmp_path))
    return load(PROFILE).groups


def test_settings_the_readme_names_exist(groups):
    # Written as "(settings: List → Map starts at)".
    named = re.findall(r"settings: ([^→()]+?) → ([^()]+?)\)", README)
    settings = {(g.name, s.label) for g in groups for s in g.settings}
    assert [n for n in named if n not in settings] == []


def test_focused_parts_are_settings_groups(groups):
    # `,` opens the settings on the part with focus (NotesScreen.focused_part),
    # so each part must be a group in palace's profile.
    assert {"list", "calendar", "preview"} <= {g.id for g in groups}


def api(package) -> list[str]:
    """What a package offers: the names it exports (functions as `name(`)
    and, for its classes, their options (parameters with a default, as
    `name=`; a dataclass's fields are data, not options) and methods (as
    `name(`; a widget's are mostly Textual's own hooks, so not those)."""
    names = [f"{name}(" if inspect.isfunction(getattr(package, name)) else name for name in package.__all__]
    for cls in (getattr(package, name) for name in package.__all__):
        if not inspect.isclass(cls) or issubclass(cls, Exception):
            continue
        if not is_dataclass(cls):
            options = inspect.signature(cls).parameters.values()
            names += [f"{p.name}=" for p in options if p.default is not p.empty]
        if not issubclass(cls, Widget):
            names += [f"{name}(" for name, value in vars(cls).items() if callable(value) and not name.startswith("_")]
    return list(dict.fromkeys(names))


def mentions(text: str, name: str) -> bool:
    """Whether `text` has `name` as a word of its own: `get(`, not `widget(`."""
    end = "" if name.endswith(("(", "=")) else r"\b"
    return re.search(rf"\b{re.escape(name)}{end}", text) is not None


@pytest.mark.parametrize("package", [mdvault], ids=lambda p: p.__name__)
def test_every_part_of_a_package_is_in_its_readme(package):
    readme = (Path(package.__file__).parents[2] / "README.md").read_text()
    assert [name for name in api(package) if not mentions(readme, name)] == []


def test_every_key_is_in_the_help():
    # The help palace gives hill-ops names keys as README.md does, but bare.
    helped = " ".join(key for _, entries in hill.app.HELP for key, _ in entries)
    shown = {KEYS.get(key, key).strip("`") for key in bound_keys()}
    assert sorted(key for key in shown if key not in helped) == []


def test_every_command_in_the_key_tree_is_a_command():
    names = {name for name, *_ in hill.app.COMMANDS}
    leaves = []

    def walk(nodes, path):
        for key, _, below in nodes:
            if isinstance(below, str):
                leaves.append((f"{path} {key}", below))
            else:
                walk(below, f"{path} {key}")

    walk(hill.app.TREE, "Space")
    assert [line for _, line in leaves if line.split()[0] not in names] == []
