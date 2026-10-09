from datetime import date, datetime

import pytest

from mdvault import Vault, markdown_links, parse, safe_filename, wikilinks


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_parse_splits_frontmatter():
    meta, body = parse("---\ntitle: Root\ntags: [a, b]\n---\nHello\n")
    assert meta == {"title": "Root", "tags": ["a", "b"]}
    assert body == "Hello\n"


def test_parse_without_or_with_broken_frontmatter():
    assert parse("Just text") == ({}, "Just text")
    assert parse("---\n: [broken\n---\nbody") == ({}, "---\n: [broken\n---\nbody")
    assert parse("---\nno end") == ({}, "---\nno end")


def test_wikilinks_drop_headings_and_aliases():
    text = "See [[Projects]], [[garden#Setup|the setup]] and [[ Root |home]]."
    assert wikilinks(text) == ["Projects", "garden", "Root"]


def test_safe_filename():
    assert safe_filename('a/b: c?') == "a-b- c-"
    assert safe_filename("  ") == "Untitled"


def test_notes_titles_tags_links_and_hidden_files(tmp_path):
    write(tmp_path / "Root.md", "---\ntitle: Home\nupdated_at: 1790685222\n---\n[[Projects]]\n")
    write(tmp_path / "Projects.md", "---\ntags: project, list\nstatus: Waiting\nsince: 2026-10-05\n---\n# Local\n[[Root]]\n")
    write(tmp_path / "sub" / "deep.md", "plain")
    write(tmp_path / ".app" / "templates" / "x.md", "hidden")
    write(tmp_path / ".hidden.md", "hidden")
    write(tmp_path / "picture.png", "not a note")
    write(tmp_path / "app" / "node_modules" / "lib" / "README.md", "a dependency")
    notes = Vault(tmp_path).notes()
    assert [n.title for n in notes] == ["deep", "Home", "Projects"]
    deep, home, projects = notes
    assert deep.relpath == "sub/deep.md" and deep.folder == "sub"
    assert home.links == ["Projects"]
    assert home.modified == datetime.fromtimestamp(1790685222)
    assert projects.tags == ["project", "list"]
    assert projects.status == "waiting" and home.status == ""
    assert projects.since == date(2026, 10, 5) and home.since is None
    assert projects.body == "# Local\n[[Root]]\n"


def test_decisions_count_the_open_ones_in_before(tmp_path):
    write(tmp_path / "item.md", "# Item\n## Goal\n- [ ] Decide: not in Before\n## Before\n"
          "- [ ] Decide: one?\n- [x] Decided 2026-10-05: two (Pierre)\n  * [ ] Decide: three?\n"
          "- [ ] [palace 012](012.md) done: needed\n```\n- [ ] Decide: an example\n```\n"
          "### Sub\n- [ ] Decide: four?\n## Notes\n- [ ] Decide: not in Before\n")
    write(tmp_path / "plain.md", "- [ ] Decide: no Before section\n")
    item, plain = Vault(tmp_path).notes()
    assert item.decisions == 3 and plain.decisions == 0


def test_resolve_backlinks_and_activity(tmp_path):
    write(tmp_path / "Root.md", "[[Projects]]")
    write(tmp_path / "Projects.md", "[[root]]")
    write(tmp_path / "garden.md", "Listed in [[Projects]].")
    vault = Vault(tmp_path)
    notes = vault.notes()
    projects = vault.resolve("projects", notes)
    assert projects.title == "Projects"
    assert sorted(n.title for n in vault.backlinks(projects, notes)) == ["Root", "garden"]
    assert sum(vault.activity(notes).values()) == 3


def test_link_graph(tmp_path):
    write(tmp_path / "Root.md", "[[Projects]] and [[projects]] again, [[Someday]]")
    write(tmp_path / "Projects.md", "[[garden]] [[Root]]")
    write(tmp_path / "sub" / "garden.md", "---\ntitle: garden\n---\n[[Projects#List|back]]")
    write(tmp_path / "Loose.md", "nothing links here")
    vault = Vault(tmp_path)
    graph = vault.graph()
    root = vault.resolve("Root")
    projects, garden = vault.resolve("Projects"), vault.resolve("garden")
    # In order, each once, with a missing note kept.
    links = graph.links_from(graph.notes[[n.title for n in graph.notes].index("Root")])
    assert [(l.target, l.note.title if l.note else None) for l in links] == [("Projects", "Projects"), ("Someday", None)]
    assert sorted(n.title for n in graph.links_to(projects)) == ["Root", "garden"]
    assert [n.title for n in graph.links_to(garden)] == ["Projects"]
    assert [n.title for n in graph.unlinked(exclude=root)] == ["Loose"]


def test_create_never_overwrites(tmp_path):
    vault = Vault(tmp_path)
    first = vault.create("Ideas: new")
    second = vault.create("Ideas: new")
    assert first.path.name == "Ideas- new.md"
    assert second.path.name == "Ideas- new 2.md"
    assert first.path.read_text() == ""


def test_rename_moves_file_and_updates_frontmatter_title(tmp_path):
    write(tmp_path / "Untitled note.md", "---\ntitle: Untitled note\ntags: []\n---\nbody\n")
    vault = Vault(tmp_path)
    note = vault.notes()[0]
    renamed = vault.rename(note, "Local projects")
    assert renamed.path.name == "Local projects.md"
    assert not (tmp_path / "Untitled note.md").exists()
    assert renamed.title == "Local projects"
    assert renamed.body == "body\n"


def test_rename_plain_note(tmp_path):
    write(tmp_path / "a.md", "text")
    vault = Vault(tmp_path)
    renamed = vault.rename(vault.notes()[0], "b")
    assert renamed.path.name == "b.md" and renamed.title == "b"


def test_markdown_links_are_local_targets_outside_code():
    text = """See [guide](docs/guide.md#setup), [spaced](<my note.md>) and [pct](my%20note.md).
[web](https://example.com/a.md) [mail](mailto:a@b.c) [top](#intro) ![img](pic.png)
`[not](inline.md)` and a block:

```
[not](fenced.md) [[NotEither]]
```
[[Real]]
"""
    # Not the web, mail or same-page links, the image, or anything in code.
    assert markdown_links(text) == ["docs/guide.md", "my note.md", "my note.md"]
    assert wikilinks(text) == ["Real"]


def test_markdown_links_resolve_like_a_file_browser(tmp_path):
    write(tmp_path / "README.md", (
        "[[Notes]] then [guide](docs/guide.md), [docs](docs/), [again](./docs/guide.md),\n"
        "[code](src/main.py), [outside](../elsewhere.md), [later](plans/next.md), [root](/Notes.md)\n"
    ))
    write(tmp_path / "Notes.md", "---\ntitle: Notes\n---\n")
    write(tmp_path / "docs" / "guide.md", "[up](../README.md)")
    write(tmp_path / "docs" / "README.md", "the docs")
    write(tmp_path / "src" / "main.py", "print()")
    vault = Vault(tmp_path)
    graph = vault.graph()
    readme = vault.resolve("README")
    links = [(l.target, l.note.relpath if l.note else None, l.path) for l in graph.links_from(readme)]
    assert links == [
        ("Notes", "Notes.md", None),                      # [[Notes]] and later /Notes.md: once
        ("docs/guide.md", "docs/guide.md", "docs/guide.md"),
        ("docs/README.md", "docs/README.md", "docs/README.md"),  # a folder is its README
        ("plans/next.md", None, "plans/next.md"),         # no note yet, but a place for it
    ]
    guide = vault.resolve("docs/guide")
    assert [n.relpath for n in graph.links_to(guide)] == ["README.md"]
    assert [n.relpath for n in graph.links_to(readme)] == ["docs/guide.md"]


def test_folders_include_ones_without_notes(tmp_path):
    write(tmp_path / "with" / "README.md", "x")
    (tmp_path / "Without").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "node_modules").mkdir()
    write(tmp_path / "top.md", "x")
    assert Vault(tmp_path).folders() == ["with", "Without"]


def test_stamp_changes_with_notes_and_folders(tmp_path):
    vault = Vault(tmp_path)
    note = write(tmp_path / "a.md", "one")
    write(tmp_path / "node_modules" / "x.md", "skipped")
    stamp = vault.stamp()
    assert vault.stamp() == stamp
    (tmp_path / "picture.png").write_text("not a note")
    assert vault.stamp() == stamp
    for change in (lambda: note.write_text("two, longer"),
                   lambda: (tmp_path / "project").mkdir(),
                   lambda: write(tmp_path / "project" / "b.md", "new"),
                   lambda: note.unlink()):
        change()
        assert vault.stamp() != stamp
        stamp = vault.stamp()


def test_update_sets_frontmatter_fields_and_keeps_the_rest(tmp_path):
    (tmp_path / "item.md").write_text("---\nstatus: open  # pink\nsince: 2026-10-01\ncreated: 2026-10-01\n---\n# Item\n")
    (tmp_path / "plain.md").write_text("# Plain\n")
    vault = Vault(tmp_path)
    item = next(n for n in vault.notes() if n.relpath == "item.md")
    item = vault.update(item, {"status": "doing", "since": date(2026, 10, 5), "next": "a step: then more"})
    assert item.path.read_text() == (
        "---\nstatus: doing\nsince: 2026-10-05\ncreated: 2026-10-01\nnext: 'a step: then more'\n---\n# Item\n"
    )
    assert item.status == "doing" and item.read()[0]["next"] == "a step: then more"
    plain = next(n for n in vault.notes() if n.relpath == "plain.md")
    assert vault.update(plain, {"status": "open"}).path.read_text() == "---\nstatus: open\n---\n# Plain\n"
    assert not list(tmp_path.glob(".*"))
