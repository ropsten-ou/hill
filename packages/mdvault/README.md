# mdvault

Read and change an Obsidian-style vault: a folder of Markdown notes with
optional YAML frontmatter, linked by `[[wikilinks]]` or ordinary Markdown
links. No UI and no index files; the folder is the source of truth, so the
same vault keeps working in Obsidian or a plain editor.

```python
from mdvault import Vault

vault = Vault("~/notes")
notes = vault.notes()                     # every Note, by title
vault.folders()                           # the top-level folders, with notes or not
projects = vault.resolve("Projects", notes)
projects.read()                           # (frontmatter, body), fresh from disk
vault.backlinks(projects, notes)          # notes that link to it
graph = vault.graph(notes)                # a LinkGraph: all links, resolved once
graph.links_from(projects)                # [Link(target, note or None, path)], in order
graph.links_to(projects)                  # notes linking to it
graph.unlinked(exclude=home)              # notes nothing links to
vault.activity(notes)                     # Counter: notes by the day they last changed
note = vault.create("New idea", "ideas")  # in ideas/; never overwrites a note
note = vault.rename(note, "Better name")  # renames the file, updates `title:`
note = vault.update(note, {"status": "done"})  # sets frontmatter fields, one line each
vault.stamp()                             # changes when notes or folders do
```

- **Notes:** `.md` files, unless `Vault` is given other `suffixes=`. A
  `Note` has its `path`, `relpath` (from the vault's top, with `/`),
  `folder`, `title`, `tags`, `refs` (its links in text order; `links` has
  just the `[[link]]` targets) and `modified` time; `note.body` reads it
  from disk.
- **Titles:** a note's frontmatter `title:` if it has one, otherwise its file
  name.
- **Tags:** `tags:` in the frontmatter, as a list or comma-separated, with or
  without `#`; `pinned: true` sets `note.pinned`.
- **Status:** `status:` in the frontmatter, lowercased, sets `note.status`
  (`""` without one); work items use it, e.g. `status: waiting`. `since:`,
  the day the status last changed (`since: 2026-10-05`), sets
  `note.since`, a date (None without one, or if it isn't a date).
- **Decisions:** `note.decisions` counts the decisions waiting in the
  body's `## Before` section, its unticked `- [ ] Decide:` lines (0
  without any); a ticked `- [x] Decided …` line no longer counts.
- **Watching:** `stamp()` stats the notes rather than reading them: their
  paths, sizes and modification times, and the top-level folders. Poll it
  and read the notes again when it changes, to follow edits made outside
  your program.
- **Modified time:** a numeric `updated_at:` (Unix time), otherwise the
  file's modification time.
- **Links:** `[[Title]]` links resolve by title (or path). Ordinary links
  like `[guide](docs/guide.md)` count when they point to a note in the
  vault: relative or from the vault's top (`/Notes.md`), with `#anchors`,
  `%20` or `<...>` allowed; a link to a folder counts as a link to its
  README. Web links, images and links to other files don't count, nor do
  links inside code. A link to a note that doesn't exist yet is kept, with
  the note set to None.
- **Renaming** renames the file and updates a `title:` in the frontmatter,
  but leaves links to the note as they are, so they then point to a note
  that doesn't exist yet.
- **Updating** sets frontmatter fields: a field's line is replaced where
  the note has one, else the field is added at the end of the frontmatter
  (made if the note has none); the rest of the file stays as it was, so
  comments and the order of fields survive. Only a field's first line is
  replaced, so keep the fields you update on one line.
- **Skipped:** hidden files and folders, and dependency or build folders
  (`node_modules`, `target`, `venv`, … see `SKIP_DIRS`, or give `Vault` your
  own as `skip_dirs=`).
- **Helpers:** `parse(text)` splits a note into frontmatter and body;
  `set_fields(text, fields)` gives the text with those frontmatter fields
  set, as `update()` writes it; `wikilinks(text)` and
  `markdown_links(text)` give a text's links, in order, outside code; `safe_filename(title)` turns a title into a file
  name.
