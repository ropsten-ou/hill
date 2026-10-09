"""palace's reference, in Markdown: its settings from its profile, and its
keys, commands, help and key tree as it gives them to hill-ops in hello, all
of them, whatever the folder's scripts. hill-ops writes it
(`hill_ops.reference.document`); palace commits it as docs/reference.md,
and tests/test_reference.py fails when that's out of date:

    uv run python -m hill.reference > docs/reference.md
"""

from __future__ import annotations

from hill_ops.index import AppDocs
from hill_ops.profiles import load
from hill_ops.reference import document

from .app import APP_KEYS, offered
from .config import PROFILE


def reference() -> tuple[str, list[str]]:
    """palace's reference, and what in it is undocumented."""
    profile = load(PROFILE)
    commands, help, tree = offered()
    docs = AppDocs("palace", keys=[list(k) for k in APP_KEYS], commands=[list(c) for c in commands],
                   help=[[part, [list(e) for e in entries]] for part, entries in help], tree=_lists(tree))
    themes = {id(profile.theme[1])} if profile.theme else set()
    return document("palace", profile.groups, docs, themes)


def _lists(tree: list) -> list:
    """TREE as hello sends it: lists, not tuples."""
    return [[key, name, _lists(under) if isinstance(under, list) else under] for key, name, under in tree]


if __name__ == "__main__":
    import sys

    text, gaps = reference()
    print(text, end="")
    for gap in gaps:
        print(gap, file=sys.stderr)
