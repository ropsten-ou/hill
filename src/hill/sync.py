"""Commit the vault's notes and documents to git and push them."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path

# What palace commits: notes, documents, diagrams and schema files. Code,
# data, images and build output stay out of its commits.
DOCUMENTS = (
    # notes
    "*.md", "*.markdown", "*.txt",
    # documents
    "*.pdf", "*.docx", "*.odt", "*.rtf", "*.pptx", "*.odp", "*.xlsx", "*.ods",
    # diagrams
    "*.canvas", "*.excalidraw", "*.drawio", "*.svg", "*.mmd", "*.puml",
    # schema files
    "*.schema.json", "*.schema.yaml", "*.schema.yml",
)
# The subject of palace's own commits, as sync_vault writes them.
PALACE_COMMIT = re.compile(r"palace: \d+ changes?")


def _git(vault: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess | None:
    # Without the optional locks, palace's status in the background never
    # holds index.lock when Claude or you commit, which would make that fail.
    try:
        return subprocess.run(["git", "--no-optional-locks", *args], cwd=vault, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def is_document(path: str) -> bool:
    name = path.rpartition("/")[2].lower()
    return any(fnmatch.fnmatch(name, pattern) for pattern in DOCUMENTS)


def repo_root(path: Path) -> Path | None:
    """The top folder of the git repository `path` is in, if any."""
    top = _git(path, "rev-parse", "--show-toplevel", timeout=5)
    if top is None or top.returncode != 0 or not top.stdout.strip():
        return None
    return Path(top.stdout.strip())


def document_changes(vault: Path) -> list[str] | None:
    """Changed notes and documents (new, edited, deleted, both sides of a
    rename), as paths from the repository's top; None if not in git."""
    status = _git(vault, "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=10)
    if status is None or status.returncode != 0:
        return None
    fields = status.stdout.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            # With -z a rename lists the new path, then the old one.
            paths.append(fields[i])
            i += 1
    return sorted({p for p in paths if is_document(p)})


def unpushed(vault: Path) -> list[str]:
    """The subjects of the commits not pushed yet, newest first; none when
    the branch has no remote."""
    log = _git(vault, "log", "--format=%s", "@{upstream}..HEAD", timeout=10)
    if log is None or log.returncode != 0:
        return []
    return log.stdout.splitlines()


def done_states(paths: list[Path], roots: dict[Path, Path | None] | None = None) -> dict[Path, str]:
    """How far done work items (their files, `paths`) have got in git:
    "to commit" while the file has changes not committed, "to push" while
    a commit to it isn't pushed, else "done" (also outside git, or on a
    branch with no remote). `roots` caches each folder's repository."""
    roots = {} if roots is None else roots
    by_repo: dict[Path | None, list[Path]] = {}
    for path in paths:
        if path.parent not in roots:
            roots[path.parent] = repo_root(path.parent)
        by_repo.setdefault(roots[path.parent], []).append(path)
    states: dict[Path, str] = {}
    for root, items in by_repo.items():
        if root is None:
            states.update(dict.fromkeys(items, "done"))
            continue
        top = root.resolve()
        rel = {path: path.resolve().relative_to(top).as_posix() for path in items}
        status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *rel.values(), timeout=10)
        dirty = {entry[3:] for entry in status.stdout.split("\0") if len(entry) > 3} if status and status.returncode == 0 else set()
        log = _git(root, "log", "--name-only", "--format=", "@{upstream}..HEAD", "--", *rel.values(), timeout=10)
        ahead = set(log.stdout.splitlines()) if log and log.returncode == 0 else set()
        for path, name in rel.items():
            states[path] = "to commit" if name in dirty else "to push" if name in ahead else "done"
    return states


def last_commits(paths: list[Path], roots: dict[Path, Path | None] | None = None) -> dict[Path, date]:
    """The day of the last commit to each file in `paths`, for those in git
    with one. `roots` caches each folder's repository, as for done_states."""
    roots = {} if roots is None else roots
    by_repo: dict[Path, list[Path]] = {}
    for path in paths:
        if path.parent not in roots:
            roots[path.parent] = repo_root(path.parent)
        if (root := roots[path.parent]) is not None:
            by_repo.setdefault(root, []).append(path)
    days: dict[Path, date] = {}
    for root, items in by_repo.items():
        top = root.resolve()
        by_name = {path.resolve().relative_to(top).as_posix(): path for path in items}
        log = _git(root, "log", "--format=%x00%cs", "--name-only", "--", *by_name, timeout=10)
        if log is None or log.returncode != 0:
            continue
        day = None
        for line in log.stdout.splitlines():
            if line.startswith("\0"):
                day = date.fromisoformat(line[1:])
            elif line in by_name and by_name[line] not in days and day is not None:
                days[by_name[line]] = day  # newest first: the first is the last
    return days


def last_commit_at(path: Path) -> float | None:
    """When `path` was last committed, as a timestamp; None if never, or
    not in git."""
    log = _git(path.parent, "log", "-1", "--format=%ct", "--", path.name, timeout=5)
    if log is None or log.returncode != 0 or not log.stdout.strip().isdigit():
        return None
    return float(log.stdout.strip())


def git_state(vault: Path) -> str:
    """How the vault's notes stand against git, e.g. "2 changes not committed"."""
    branch = _git(vault, "status", "--porcelain=v1", "--branch", "--untracked-files=no", timeout=5)
    if branch is None or branch.returncode != 0:
        return "not in git"
    head = branch.stdout.splitlines()[0] if branch.stdout else ""
    changes = document_changes(vault) or []
    parts = []
    if changes:
        parts.append(f"{len(changes)} change{'s' * (len(changes) != 1)} not committed")
    if m := re.search(r"ahead (\d+)", head):
        parts.append(f"{m[1]} commit{'s' * (m[1] != '1')} not pushed")
    if m := re.search(r"behind (\d+)", head):
        parts.append(f"{m[1]} to pull")
    if "..." not in head:
        parts.append("no remote")
    elif not changes and "ahead" not in head:
        parts.append("all pushed")
    return " · ".join(parts)


def sync_vault(
    vault: Path, commit: bool = True, push: bool = True, failed: Callable[[str], None] = lambda what: None,
) -> str:
    """Commit changed notes and documents (see DOCUMENTS), then push
    palace's commits. Returns a one-line report, empty if there was nothing
    to do; `failed` hears "commit failed" or "push failed", without git's
    message, which can name a note.

    Only a folder that is a repository's top folder is synced: for a folder
    inside a bigger repository (a code project, say), committing would
    sweep up unrelated work. Anything else already staged is left staged
    and out of the commit. Commits of your own are yours to push: while one
    is waiting, palace doesn't push, since its commits would take yours
    along."""
    top = repo_root(vault)
    if top is None or top.resolve() != vault.resolve():
        return ""
    changes = document_changes(vault)
    if changes is None:
        return ""
    report = []
    if commit and changes:
        count = len(changes)
        _git(vault, "add", "-A", "--", *changes)
        done = _git(vault, "commit", "-q", "-m", f"palace: {count} change{'s' * (count != 1)}", "--", *changes)
        if done is None or done.returncode != 0:
            failed("commit failed")
            return "palace: commit failed" + (f": {done.stderr.strip()}" if done else "")
        report.append(f"committed {count} change{'s' * (count != 1)}")
    if push:
        waiting = unpushed(vault)
        yours = [subject for subject in waiting if not PALACE_COMMIT.fullmatch(subject)]
        if waiting and not yours:
            pushed = _git(vault, "push", "-q")
            if pushed is None or pushed.returncode != 0:
                failed("push failed")
                report.append("push failed" + (f": {pushed.stderr.strip()}" if pushed else ""))
            else:
                report.append("pushed")
        elif yours and len(yours) < len(waiting):
            count = len(yours)
            report.append(f"not pushed, with {count} commit{'s' * (count != 1)} of your own waiting")
    return "palace: " + " · ".join(report) if report else ""
