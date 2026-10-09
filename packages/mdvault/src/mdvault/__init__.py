"""Read and change an Obsidian-style vault: a folder of Markdown notes with
optional YAML frontmatter, linked by [[wikilinks]] or ordinary Markdown links.

No UI, no index files: the folder is the source of truth, so the same vault
works in Obsidian, a text editor or anything built on this.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from urllib.parse import unquote
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

__all__ = [
    "SKIP_DIRS", "Link", "LinkGraph", "Note", "Vault",
    "markdown_links", "parse", "safe_filename", "set_fields", "wikilinks",
]

# [[Target]], [[Target|shown text]], [[Target#Heading]], [[Target#Heading|shown]],
# or [text](target), [text](<target with spaces>), [text](target "title");
# not images, ![alt](picture.png).
_LINK = re.compile(
    r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]"
    r"|(?<!!)\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)"
)
# Fenced code blocks and inline code: links in them are examples, not links.
_CODE = re.compile(r"(?ms)^(```|~~~).*?^\1|`[^`\n]*`")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_UNSAFE = re.compile(r'[/\\:*?"<>|]')


def parse(text: str) -> tuple[dict, str]:
    """Split a note into its frontmatter (a dict, empty if none) and body."""
    if text.startswith("---\n") or text.startswith("---\r\n"):
        end = re.search(r"\r?\n---[ \t]*(\r?\n|$)", text[3:])
        if end:
            raw = text[3 : 3 + end.start()]
            try:
                meta = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                return {}, text
            if isinstance(meta, dict):
                return meta, text[3 + end.end() :]
    return {}, text


def set_fields(text: str, fields: dict) -> str:
    """`text` with each of `fields` set in its frontmatter, one line each:
    a key's line is replaced where it has one, else the key is added at the
    end; a note without frontmatter gets some. Everything else stays as it
    was, comments and order included."""
    lines = {key: f"{key}: " + yaml.safe_dump(value, default_flow_style=True).splitlines()[0]
             for key, value in fields.items()}
    end = re.search(r"\r?\n---[ \t]*(\r?\n|$)", text[3:]) if text.startswith("---\n") or text.startswith("---\r\n") else None
    if end is None:
        return "---\n" + "".join(f"{line}\n" for line in lines.values()) + "---\n" + text
    head, rest = text[: 3 + end.start()], text[3 + end.start() :]
    for key, line in lines.items():
        pattern = re.compile(rf"(?m)^{re.escape(key)}:.*$")
        head = pattern.sub(lambda _: line, head, count=1) if pattern.search(head) else f"{head}\n{line}"
    return head + rest


def _links(text: str) -> list[tuple[str, str]]:
    """("wiki", title) and ("markdown", target) pairs, in order, outside code."""
    found = []
    for m in _LINK.finditer(_CODE.sub(" ", text)):
        if m.group(1) is not None:
            found.append(("wiki", m.group(1).strip()))
        else:
            found.append(("markdown", m.group(2)))
    return found


def wikilinks(text: str) -> list[str]:
    """[[link]] targets in order, without headings or aliases, outside code."""
    return [target for kind, target in _links(text) if kind == "wiki"]


def _local_target(target: str) -> str | None:
    """A Markdown link's target as a local path: anchors and <> dropped,
    %-escapes decoded; None for web addresses and same-page anchors."""
    target = target.strip().removeprefix("<").removesuffix(">")
    if _SCHEME.match(target):
        return None
    target = unquote(target.partition("#")[0].partition("?")[0])
    return target or None


def markdown_links(text: str) -> list[str]:
    """Local targets of ordinary Markdown links, in order, outside code."""
    targets = (_local_target(t) for kind, t in _links(text) if kind == "markdown")
    return [t for t in targets if t is not None]


def safe_filename(title: str) -> str:
    """A file name for a title: characters file systems reject become '-'."""
    name = _UNSAFE.sub("-", title).strip().strip(".")
    return name or "Untitled"


def _tags(value: object) -> list[str]:
    if isinstance(value, str):
        return [t.strip().lstrip("#") for t in value.split(",") if t.strip()]
    if isinstance(value, list):
        return [str(t).lstrip("#") for t in value if t is not None]
    return []


def _date(value: object) -> date | None:
    """A frontmatter date: YAML reads 2026-10-05 as one, quoted it's text."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()) if value else None
    except ValueError:
        return None


@dataclass
class Note:
    path: Path
    relpath: str
    """Path inside the vault, with '/' separators, e.g. "projects/garden.md"."""
    title: str
    tags: list[str] = field(default_factory=list)
    refs: list[tuple[str, str]] = field(default_factory=list)
    """Links in order: ("wiki", title) for [[links]], ("path", path in the
    vault) for Markdown links to notes, even ones that don't exist yet."""
    modified: datetime = field(default_factory=datetime.now)
    pinned: bool = False
    status: str = ""
    """The frontmatter's `status:`, lowercased, e.g. "waiting"; "" if none."""
    since: date | None = None
    """The frontmatter's `since:`, the day the status last changed; None if
    none, or not a date."""
    decisions: int = 0
    """How many decisions wait in the body's `## Before` section: its
    unticked `- [ ] Decide:` lines."""

    @property
    def links(self) -> list[str]:
        """The [[link]] targets."""
        return [target for kind, target in self.refs if kind == "wiki"]

    @property
    def folder(self) -> str:
        return self.relpath.rpartition("/")[0]

    def read(self) -> tuple[dict, str]:
        """The note's frontmatter and body, read fresh from disk."""
        return parse(self.path.read_text(errors="replace"))

    @property
    def body(self) -> str:
        return self.read()[1]


def _note_path(root: Path, note: Path, target: str, suffixes: tuple[str, ...]) -> str | None:
    """Where a Markdown link from `note` points, as a path in the vault, if
    it points to a note there (a folder counts as its README)."""
    base = root if target.startswith("/") else note.parent
    path = Path(os.path.normpath(base / target.lstrip("/")))
    if os.path.commonpath([root, path]) != str(root):
        return None
    if path.is_dir():
        path = next((path / n for n in ("README.md", "readme.md", "index.md") if (path / n).is_file()), path)
    if path.suffix not in suffixes:
        return None
    return path.relative_to(root).as_posix()


def _load(root: Path, path: Path, suffixes: tuple[str, ...] = (".md",)) -> Note:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        text = ""
    meta, body = parse(text)
    title = meta.get("title")
    updated = meta.get("updated_at")
    if isinstance(updated, (int, float)) and not isinstance(updated, bool):
        modified = datetime.fromtimestamp(updated)
    else:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
    return Note(
        path=path,
        relpath=path.relative_to(root).as_posix(),
        title=str(title) if title else path.stem,
        tags=_tags(meta.get("tags")),
        refs=_refs(root, path, body, suffixes),
        modified=modified,
        pinned=meta.get("pinned") is True,
        status=str(meta.get("status") or "").strip().casefold(),
        since=_date(meta.get("since")),
        decisions=_decisions(body),
    )


# A `## Before` section, up to the next heading of its level or above.
_BEFORE = re.compile(r"(?ims)^##[ \t]+Before[ \t]*$(.*?)(?=^#{1,2}[ \t]|\Z)")
_DECIDE = re.compile(r"(?m)^[ \t]*[-*+][ \t]+\[ \][ \t]+Decide:")


def _decisions(body: str) -> int:
    """The unticked `- [ ] Decide:` lines in a body's `## Before` section,
    outside code."""
    before = _BEFORE.search(_CODE.sub(" ", body))
    return len(_DECIDE.findall(before.group(1))) if before else 0


def _refs(root: Path, path: Path, body: str, suffixes: tuple[str, ...]) -> list[tuple[str, str]]:
    refs = []
    for kind, target in _links(body):
        if kind == "wiki":
            refs.append(("wiki", target))
        elif (local := _local_target(target)) and (where := _note_path(root, path, local, suffixes)):
            refs.append(("path", where))
    return refs


@dataclass(frozen=True)
class Link:
    target: str
    """A [[link]]'s title as written, or a Markdown link's path in the vault."""
    note: Note | None
    """The note it points to, or None if there is no such note yet."""
    path: str | None = None
    """For a Markdown link, the path in the vault it points to."""


class LinkGraph:
    """Who links to whom, resolved once for a list of notes."""

    def __init__(self, notes: list[Note]) -> None:
        self.notes = notes
        index: dict[str, Note] = {}
        for note in notes:
            for key in (note.title, note.path.stem, note.relpath.removesuffix(".md")):
                index.setdefault(key.casefold(), note)
        paths = {n.relpath.casefold(): n for n in notes}
        self._out: dict[str, list[Link]] = {}
        self._in: dict[str, list[Note]] = {n.relpath: [] for n in notes}
        for note in notes:
            links, seen = [], set()
            for kind, target in note.refs:
                found = (index if kind == "wiki" else paths).get(target.casefold())
                # Each note once, however many times and ways it's linked.
                key = found.relpath if found is not None else (kind, target.casefold())
                if key in seen:
                    continue
                seen.add(key)
                links.append(Link(target, found, target if kind == "path" else None))
                if found is not None and found is not note:
                    self._in[found.relpath].append(note)
            self._out[note.relpath] = links

    def links_from(self, note: Note) -> list[Link]:
        """The note's links, in order, each once, including missing notes."""
        return self._out.get(note.relpath, [])

    def links_to(self, note: Note) -> list[Note]:
        """Notes that link to `note`."""
        return self._in.get(note.relpath, [])

    def unlinked(self, exclude: Note | None = None) -> list[Note]:
        """Notes nothing links to, apart from `exclude` (e.g. the home note)."""
        return [n for n in self.notes if not self._in.get(n.relpath) and n is not exclude]


# Folders of code projects that hold dependencies or build output, whose
# READMEs and changelogs aren't notes.
SKIP_DIRS = frozenset(
    "node_modules target build dist venv site-packages vendor __pycache__".split()
)


class Vault:
    def __init__(
        self,
        root: Path | str,
        suffixes: tuple[str, ...] = (".md",),
        skip_dirs: frozenset[str] = SKIP_DIRS,
    ) -> None:
        self.root = Path(root).expanduser()
        self.suffixes = suffixes
        self.skip_dirs = skip_dirs

    def _paths(self):
        for dirpath, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in self.skip_dirs)
            for name in files:
                if not name.startswith(".") and Path(name).suffix in self.suffixes:
                    yield Path(dirpath) / name

    def notes(self) -> list[Note]:
        """Every note, sorted by title. Hidden files and folders, and
        dependency and build folders (`skip_dirs`), are skipped."""
        found = [_load(self.root, path, self.suffixes) for path in self._paths()]
        return sorted(found, key=lambda n: (n.title.casefold(), n.relpath))

    def stamp(self) -> tuple:
        """What the vault looks like, without reading its notes: their paths,
        sizes and modification times, and the top-level folders. While it
        stays the same, so do `notes()` and `folders()`, so a program can
        poll it to notice changes made by others."""
        found = []
        for path in self._paths():
            try:
                st = path.stat()
            except OSError:
                continue
            found.append((path.relative_to(self.root).as_posix(), st.st_size, st.st_mtime_ns))
        return tuple(sorted(found)), tuple(self.folders())

    def folders(self) -> list[str]:
        """The vault's top-level folders, notes or not (in a folder of
        projects, the projects), skipping the same folders as `notes()`."""
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            return []
        return sorted(
            (e.name for e in entries if e.is_dir() and not e.name.startswith(".") and e.name not in self.skip_dirs),
            key=str.casefold,
        )

    def resolve(self, link: str, notes: list[Note] | None = None) -> Note | None:
        """The note a [[link]] points to: by title, else by path."""
        notes = self.notes() if notes is None else notes
        key = link.strip().casefold()
        for note in notes:
            if note.title.casefold() == key:
                return note
        for note in notes:
            if note.relpath.casefold().removesuffix(".md") == key.removesuffix(".md"):
                return note
        return None

    def backlinks(self, note: Note, notes: list[Note] | None = None) -> list[Note]:
        """Notes that link to `note`."""
        return self.graph(notes).links_to(note)

    def graph(self, notes: list[Note] | None = None) -> LinkGraph:
        """Links between notes, resolved once."""
        return LinkGraph(self.notes() if notes is None else notes)

    def activity(self, notes: list[Note] | None = None) -> Counter[date]:
        """How many notes were last changed on each day."""
        notes = self.notes() if notes is None else notes
        return Counter(n.modified.date() for n in notes)

    def _free_path(self, folder: str, title: str, suffix: str, keep: Path | None = None) -> Path:
        base = self.root / folder if folder else self.root
        stem = safe_filename(title)
        path = base / f"{stem}{suffix}"
        n = 2
        while path.exists() and path != keep:
            path = base / f"{stem} {n}{suffix}"
            n += 1
        return path

    def create(self, title: str, folder: str = "") -> Note:
        """A new, empty note named after `title` (never overwrites a note)."""
        path = self._free_path(folder, title, ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x"):
            pass
        return _load(self.root, path, self.suffixes)

    def update(self, note: Note, fields: dict) -> Note:
        """Set `fields` in `note`'s frontmatter (set_fields), replacing the
        file in one go, and give the note as it is now."""
        text = set_fields(note.path.read_text(errors="replace"), fields)
        new = note.path.with_name(f".{note.path.name}.{os.getpid()}")
        new.write_text(text)
        new.replace(note.path)
        return _load(self.root, note.path, self.suffixes)

    def rename(self, note: Note, title: str) -> Note:
        """Give `note` a new title: rename its file, and update a `title:`
        in its frontmatter if it has one. Links to it are not rewritten."""
        text = note.path.read_text(errors="replace")
        meta, _ = parse(text)
        if "title" in meta:
            text = re.sub(r"(?m)^title:.*$", "title: " + yaml.safe_dump(title).splitlines()[0], text, count=1)
            note.path.write_text(text)
        new = self._free_path(note.folder, title, note.path.suffix, keep=note.path)
        if new != note.path:
            os.rename(note.path, new)
        return _load(self.root, new, self.suffixes)
