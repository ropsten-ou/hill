# Design

Why palace is built the way it is. What it does is in the
[README](README.md), and what each part offers in its own README:
[mdvault](packages/mdvault/README.md), and, in hill,
[keyline](https://github.com/ropsten-ou/hill-ops/blob/main/packages/keyline/README.md) and
[settings-panel](https://github.com/ropsten-ou/hill-ops/blob/main/packages/settings-panel/README.md).

The idea: work with Claude from micro, an editor with nano's simplicity,
and put that editor in a place for zooming out to see how notes and
documents connect, a mind palace of sorts.

## Principles

- **The folder is the source of truth.** Notes are plain Markdown files, and
  palace keeps no index or database beside them, so Obsidian, micro or any
  other editor can work on the same notes.
- **A real editor, with Claude in it.** Notes open in micro, where the
  micro-claude plugin talks to Claude. Inside hill, micro runs beside
  palace, over the preview; on its own, palace hands micro the terminal. It
  picks up what changed when micro exits, rather than having an editor of
  its own.
- **Claude works where the note lives.** micro runs in the note's
  repository, or else in its project folder, so Claude's session is in that
  project and reads its `CLAUDE.md`.
- **Keys where they act.** Each pane has its own key line, like micro's key
  menu, showing what can be done there; the pane in focus is lighter and
  its line highlighted. Keys for the whole app go to hill's strip.
- **Settings like a game's options, not a spreadsheet.** Every setting steps
  through a fixed list of values, so there's nothing to type and nothing to
  get wrong. They live in hill's strip, which grows under palace, opens on
  the part you were in, and applies a change as soon as it sensibly can.
- **Programs own their settings.** hill writes straight into each program's
  own config file (micro's `settings.json`), so the program stays the owner
  of its settings and there's no copy to keep in step; palace only
  describes its own, in `src/palace/hill.toml`.
- **Never lose what's there.** A new or renamed note never overwrites
  another; a settings file that can't be read is never written, and
  settings files are replaced in one step, through symlinks.
- **Commit notes, nothing else.** palace's own commits hold only notes and
  documents, and it makes them only in a folder that is a repository's top
  folder, so they never sweep up code or work in progress. It pushes only
  its own commits, and leaves yours to you.
- **Small parts, each usable on its own.** mdvault, keyline and
  settings-panel know nothing about palace; palace puts them together.
- **Tests never touch real notes or configs.** They point palace at
  temporary folders (`PALACE_VAULT`, `PALACE_CONFIG_HOME`,
  `MICRO_CONFIG_HOME`). clin's test suite, which didn't, once overwrote the
  real clin config.
- **The docs change with the code.** The READMEs say what palace does and
  change in the same commit as the code; this file says why.

## Decisions

Oldest first; new ones go at the end. Each commit has the details.

**2026-09-29 · A home screen with a settings panel** (`ed9c08d`). palace
began as a Textual screen for launching clin (a notes app), micro and a
shell, with micro-style key lines at the bottom. Settings were to feel like
an Android game, not Excel: a panel over the bottom third of the window,
groups as tabs at its bottom (or top, as in btop), each group shown as tiles
or as a list to make it easier to remember where things are and quicker to
get there, values stepped through and written straight into each program's
own config file.

**2026-09-29 · palace becomes the notes screen** (`431c008`). The aim became
to land in the notes view, with each pane's key hints at its own bottom
rather than one line for the whole screen. A fork of clin tried that first;
then palace got its own notes screen, in Textual, and the settings panel,
the key line and reading the vault became packages of their own (`ecf73ee`,
`0fe059c`).

**2026-09-29 · The links map** (`c241e4b`). A way to zoom out: from the home
note, a tree of what links to what. Cycles show as `↺` so it stays finite,
links to missing notes can be created with Enter, and notes nothing links
to are gathered at the end, to be linked in.

**2026-09-30 · `~/projects` by default; commit only notes and documents; no
clin** (`0745f86`). palace shows `~/projects`, the folder of projects,
instead of a separate `~/notes` vault (since removed). `~/projects` isn't a
repository, though some of its projects are, so palace commits only in a
folder that is a repository's top folder, and only notes, documents,
diagrams and schema files; code, data and images are left to their
projects' own commits. clin was dropped once nothing depended on it.

**2026-09-30 · Ordinary Markdown links count** (`8d1f20f`). In `~/projects`,
READMEs link with `[text](path)`, not `[[wikilinks]]`, so the map found
almost nothing. Such links now count, a link to a folder as a link to its
README; links in code don't, since README examples are full of them; and
dependency folders are skipped, since code projects fill them with READMEs.
`~/projects/Home.md` links each project's README or main document, so the
map starts there.

**2026-09-30 · Settings apply at once; the list remembers** (`4add4f7`,
`5bd72d8`). Settings apply as soon as sensible, also those changed from
micro with Alt-,. micro's are written out even when set back to their
default, since a running micro that re-reads its file wouldn't otherwise see
the change. Folders closed in the list stay closed across restarts, and map
branches stay as they were left while a note is edited.

**2026-09-30 · Settings open on the pane in focus** (`e24bb5a`, `f717b7a`).
The Notes group split into List, Calendar, Preview and Sync, so `,` opens on
the settings of the pane you're in, and micro's Alt-, on Editor, or on
Claude from the chat.

**2026-09-30 · Every project listed; micro in the project folder**
(`7c48c03`, `6ecc55b`). Projects without Markdown vanished from the list
(pexpect, whose README is reStructuredText), so every top-level folder now
shows. micro runs in the note's repository, or else in its project folder,
so Claude's session reads the project's own `CLAUDE.md` even where there's
no repository.

**2026-09-30 · The docs change with the code** (`1f77ef0`). Within a day of
palace's first commit, four changes reached the code but not the READMEs:
the `!` shell key (dropped in a rewrite while still bound), every project
being listed with micro's folder fallback, `Vault.folders()` and
`SettingsScreen(start=)`. So the READMEs now change in the same commit as
what they describe, `tests/test_docs.py` checks that they name every key,
settings group and part of each package's API, and decisions like this one
get an entry here.

**2026-09-30 · palace pushes only its own commits.** Quitting pushed the
whole branch, commits made by hand included, even with Commit on quit off.
Now palace pushes only when every commit waiting to go is its own. While
one of yours is waiting too, it leaves the push to you and says so, since
pushing its commits would take yours along.

**2026-09-30 · Settings move to hill's strip.** The aim: the settings,
help and command mode split from palace's main panel, as nano and micro
keep theirs at the bottom, with Claude later helping there. The settings
panel became [hill](https://github.com/ropsten-ou/hill-ops/blob/main/README.md), a program of its own that runs
palace, or micro, or any app, above a strip; why it's built on tmux is in
[hill's DESIGN.md](https://github.com/ropsten-ou/hill-ops/blob/main/DESIGN.md). palace describes its settings in
`src/palace/hill.toml`, says hello over hill's channel, asks for its
settings with `,` and applies `settings.changed`. Keys for the whole app
leave the panes' key lines for the strip. micro's settings went to
micro-claude's own profile, and keyline and settings-panel to hill.
Outside hill, `,` runs `hill settings` on its own.

**2026-09-30 · palace starts itself inside hill.** So that `palace` stays
the command: run in a terminal outside hill, with hill installed, it runs
`hill -- palace` in its place. Inside hill it runs as it is, so it can't
loop; `PALACE_NO_HILL=1` keeps it on its own.

**2026-09-30 · Help and the command line in hill's strip.** `?` opens
palace's help there, on the part you were in, and `:` the command line,
with palace's commands, which do what its keys do and can take an
argument (`new Ideas`, `search cb`). The prompts for a new note and a
rename are asked on the strip's line too. Search stays at the top of the
list: it acts on the list and filters as you type. Outside hill the
prompts stay at the bottom of palace.

**2026-10-01 · The preview beside palace, micro over it.** Pierre wanted
palace, its preview and Claude side by side, the settings under the
preview and Claude, and the calendar under the list. tmux panes are
rectangles, so inside hill the preview is a program of its own (`palace
_preview`), in a pane hill runs beside palace (see "Panes beside the app"
in [hill's README](https://github.com/ropsten-ou/hill-ops/blob/main/README.md)); it hears the note selected and the
theme over a socket palace opens, and passes palace's keys back. palace's
own preview stays for when it runs on its own, and shows until the pane
has joined. ⏎ runs micro over the preview, so palace stays in sight while
you edit, and the preview comes back as it was. Preview → Width went:
hill's Layout widths size the panes now. The calendar shows the weeks that
fit, since palace's column can be narrow.

**2026-10-01 · Claude on the note.** The third pane at the top is Claude
about the note shown, as micro-claude is about the file in micro: Claude
Code's agent with Pierre's own settings, in the note's project, reading
and editing its files with a y/n first. hill's Claude stays in the strip,
for help with palace itself, and never sees notes. A conversation per
project, so moving through notes in one project keeps talking, and each
answer has palace read the notes again. While micro runs, its own chat
takes the pane, so the two Claudes don't share one conversation yet. The
ACP client became a hill package (acp-client) that both use.

**2026-10-01 · Claude carries its conversations over restarts.** On the
first day, Claude on the note was in the middle of a change to palace
when the tests it ran joined Pierre's hill and stopped its pane (the
tests now start outside hill). palace came back, but nobody knew what had
been in progress, Claude least of all; Pierre asked for a "living will".
A conversation also ended whenever palace restarted, which is how Pierre
tries a change Claude made. So palace keeps a will for each project's
conversation: its Claude Code session, the last question and how its
answer ended, with the tool in use while it runs. After a restart Claude
loads the session and remembers it; an answer that never ended counts as
cut off, which the pane shows and Claude hears with the next question.
The agent still starts only when asked (or on ⏎), so moving through
notes costs nothing, and the conversation shows again then, above the
question. Ctrl-n starts afresh. A note Claude writes for itself as it
works was the other choice: readable by any Claude, but only as good as
Claude's habit of keeping it.

**2026-10-02 · The focus follows the mouse, lazily; the pane with it is
lighter.** Pierre asked for the focus to follow the mouse in all of
palace's panes, and for the lighter background the list had, which is
Textual's own look for a focused list, to show where the focus is. Until
then the preview's and Claude's key lines were always highlighted, so three
panes looked focused at once: a side pane now asks tmux whether it has the
focus when it starts, since tmux only says so when that changes. Moving the
mouse over a pane gives it the focus, and each pane takes it itself, with
hill-client's `take_focus()`, as tmux's own focus-follows-mouse would also
take it from hill's strip as you type there (see hill's DESIGN.md). It's
lazy so that it's safe to type: after a key moved the focus, a nudge of the
mouse resting over the pane it left doesn't take it back, only a move of a
few cells, since palace's keys are single letters and `q` quits, with micro
in it. A pause before the focus moves was the other way, but it makes every
deliberate move wait too. micro can't see the mouse move, so it still takes
the focus with a click.

**2026-10-02 · hill's settings panel follows the mouse too.** Pierre found
the focus didn't visibly go to the settings panel under the preview and
Claude: it looked the same with the focus as without, and the mouse moving
over it did nothing, while palace's panes did both. hill's panel now takes
the focus as the mouse moves over it and is lighter while it has it (see
hill's DESIGN.md), and the lazy rule, `Hover`, moved from palace to
hill-client, so that every pane in the window follows the mouse the same
way.

**2026-10-02 · micro follows the mouse too.** Pierre wanted the focus to
follow the mouse into micro as well, over the preview and Claude. micro
can't see the mouse move, so hill runs it through a relay that does, and
gives its pane the lighter background while it has the focus (see hill's
DESIGN.md); palace needs no change of its own for it. A click still moves
the focus between micro's editor and its chat, which are micro's own.

**2026-10-03 · The list follows the vault by polling.** Notes change outside
palace all the time: Claude sets a work item's status, a pull brings new
ones. palace now looks every second whether the vault changed, with
mdvault's `stamp()`, which only stats the notes, and reads them again when
it did. Watching the files (e.g. `watchfiles`) would notice sooner, but
needs a dependency and a native watcher, and polling also works on synced
and network folders; a stat of `~/projects` takes a few milliseconds. A
reload from outside keeps the cursor, the scroll and the preview, so it
never gets in the way of what you're doing.

**2026-10-03 · Ret, not ⏎.** The key lines, the help and the README write
the Return key as Ret, the way they write Esc and Tab. Menlo has ⏎, but at
the 8 or 9 points Pierre's terminal uses it's too thin to make out; ⎆, the
enter symbol, isn't in Menlo, so macOS takes it from Apple Symbols, half a
cell too wide. A word reads in any font, at any size.

**2026-10-04 · `e` edits; Ret and a click open folders.** Ret, and a second
click, opened the note under the cursor in micro, so moving through the
list and picking a note to edit were tangled: a Ret meant for a folder, or
a click too many, opened micro. Now only `e` edits, in the list, the map
and the preview alike, and creates a missing note in the map. Ret and a
click do what they do in any tree: select, and open or close a folder or a
map branch. With nothing to open by accident, a click no longer needs a
second one; the key line's *e edit*, which can be clicked, is the way in
with the mouse.

**2026-10-04 · A rainbow of work item states.** Pierre wanted more colours
for work items, and one place saying what they mean: red ready, orange
waiting, yellow running, green done but not committed, blue committed but
not pushed, violet all done, in the order an item goes. The three done
shades come from git, not from more statuses to keep up by hand: palace
looks at the item's file every few seconds, since a commit or a push
changes no note. The colours are fixed, not the theme's, which has no
violet, with darker ones for a light theme. The README's Work items table
and the help say what each means; `~/projects/CLAUDE.md` links there.

**2026-10-04 · Pink for open items.** Pierre wanted work in progress to
draw attention, not just items waiting on Pierre: an `open` item, filed but
still without a plan, is soon ready, so it gets pink, beside red, and the
rainbow follows an item from filing to pushed. Folders count it with the
others. `doing` stays unmarked; a mark for it would want something other
than pink, so planning and building look different.

**2026-10-05 · Work in progress in view, what's long done out of it.**
`w` shows only work items in progress, a view like a search, not a
setting, since it's on and off as you go. What stays a setting is whether
done items leave the list, and when (List → Hide done items), because
that's how you want the list every time. Only items all done or dropped
go: one to commit or push still wants something. How long an item has been
done needs a date, and the file's modified time won't do, since it moves
with every later edit; so items carry `since:`, the day their status last
changed (Pierre's idea), which also says how long one has waited. Items
from before it count from their last commit. Hiding is off by default, so
nothing leaves the list unasked.

**2026-10-05 · Restart just what's needed.** Trying a change to palace
meant quitting it, which cut off what was in flight, often the very answer
of the Claude that made the change. Pierre wanted the least disruptive
restart: only the panes the change touches, each once it's idle. Each pane
watches the files of its own loaded modules, so no map from files to panes
needs keeping up, and says *restart pending* until `:restart`. palace's
own pane runs under palace-loop, which runs it again on exit status 75, so
hill and the side panes stay (Pierre's idea); the side panes wait for it
and join its socket again, which is named after the loop, so it doesn't
move. A side pane restarts itself in place. Only a change to hill's code
restarts everything (`:restart all`, status 76, to the loop around hill).
New work in a pending pane, which would hold the restart back, asks first.
A restart doesn't commit or push: that's for quitting.

**2026-10-05 · Claude's pane knows its agent is out of date.** Another
session enabled a plugin, and the Claude pane's agent, started before,
never loaded it; nothing said so. The pane now watches what its agent
loads, as it was when the pane started: the settings a running agent
doesn't read again, by value, since other sessions write `settings.json`
all the time; the MCP servers; and the agent's package versions. For what
no file shows, Claude asks, through a file palace names in its
environment, `$PALACE_RESTART`, which `~/projects/CLAUDE.md` tells it of.
The pane only marks the restart, with the reason; it never restarts by
itself, since a restart in the middle of your thinking costs more than a
key.

**2026-10-05 · Due and dead, as signs, from the scan.** An item whose wait
is over, or whose job died, should say so without anyone opening it. The
rainbow is taken, and these aren't another step on it but news about the
step an item is at, so they are signs after the status mark, Pierre's
choice: an hourglass for due, a skull for a dead job. palace doesn't judge
`after:` or `job:` itself: `~/projects/scan` does, for every project and
both machines' jobs, and palace reads what it wrote, so the list and the
scan never disagree.

**2026-10-05 · A session per work item, and the cockpit plan.** Pierre
wanted palace's settings, the Claude help line and the hidden command line
unified, Claude there open to any request, and several work items worked
on at once. The plan (work items 016 to 019): two Claudes with different
jobs, the cockpit's in hill's strip, which steers (settings, commands,
what's due across projects) and offers commands rather than doing the
work, and work sessions, one per item, which do it. A session per item,
not per project, since a conversation is about one item (as
`~/projects/CLAUDE.md` has it); the others go on while one is shown. No
limit on two sessions editing one repository for now: work often spans
repositories, and warnings can come if it gets in the way. Once an item is
done and pushed, its idle session is wrapped: its agent stops, and a
follow-up is a new item, or reopens this one, as the rule says (see
2026-10-06, a done item offers no session).

**2026-10-05 · The Overview: palace's lines, hill's tab.** The cockpit's
view of what waits on you is a tab of hill's panel, but hill knows nothing
of work items: an app sends it lines to pick from (`overview`), and hears
which was picked, as it sends its settings in a profile and hears them
change. palace builds the lines from its own notes, whose statuses it
reads as they change, and takes only due and dead from the scan, which
runs every 15 minutes; so an item set to `waiting` shows at once. Picking
a line selects the item, which brings its session up in Claude's pane;
the panel stays open, to go down the list.

**2026-10-05 · Commands that steer the work.** Claude in the strip offers
only what palace lists as commands, so steering the work items had to
become commands: `select`, `set-status`, `work`, `sync` and `scan`. They
name an item as the Overview shows it (`palace 020`), since that's what
Claude is told and what you read, or by its path. `:work` asks "go" in the
item's session, as you would: the item says the rest, and the work stays
with Claude's pane. `:set-status` writes only `status:` and `since:`;
moving an item off its project's open list, or writing what came of it,
is a sentence of judgement, so it stays with the Claude that did the work.

**2026-10-05 · Open decisions, counted from the item.** An item now
holds the decisions that wait on Pierre in its Before section, one
`- [ ] Decide:` line each, and the list should show which items have
some, and how many, across projects, without opening them. mdvault
counts the unticked lines as it reads the note, so the count changes as
soon as a line is ticked, and palace shows ❓N after the status mark,
beside the scan's signs: a question mark, since it's news about the item
like ⏳ and 💀, not a step on the rainbow. Folders sum the decisions, not
the items, since a decision is what costs you a read. The items an item
needs aren't counted: the rule pairs each with an `after:` atom, so the
scan already says when the wait is over (⏳).

**2026-10-06 · A white heart for a dead job.** The mark for an item whose
job died is now a white heart 🤍, not a skull 💀, in the list, the map,
the folder counts and the help, on Pierre's word. The entries above keep
the skull, as it was when they were written.

**2026-10-06 · palace runs from its own .venv.** `uv tool install`
made a copy of the dependencies at install time, and nothing updated it:
when wrap-client became hill-client, `palace` failed to start until the
tool was installed again. `~/.local/bin/palace` now links to `bin/shim`,
which runs `uv sync` and then the command from palace's `.venv`, the one
`uv run pytest` uses, so there is one environment and it follows
`uv.lock`. The check costs about 30 ms when nothing has changed, and uv
locks the environment, so palaces starting together don't race. palace
doesn't try to reinstall itself when it starts: it would have to rewrite
the environment it runs from and then restart.

**2026-10-06 · The real Claude Code in Claude's pane.** Claude's pane was
palace's own chat over ACP (claude-agent-acp), 900 lines that kept up
with Claude Code by hand: permissions, a will to carry conversations on
after a crash, a watch for an agent out of date. It never had slash
commands, plan mode, `/compact`, the status line or images. Now the pane
is the `claude` command itself, a session per work item (and per project
for its other notes), and palace keeps only what it adds: which session
a note has, showing it as the note is selected, `:work`, the marks, and
wrapping a session once its item is pushed. hill runs a program for each
session and keeps those not shown running out of sight, by name (hill's
work item 005), since a running `claude` can't be moved between panes
behind hill's back. Claude Code's hooks report how each session stands
to files palace reads, rather than a protocol palace speaks, so a new
Claude Code needs nothing from palace. Each pane starts with a small
program that waits for Ret, so selecting notes starts no Claude: a
`claude` takes some hundreds of MB. palace picks a new session's id
(`--session-id`) and writes it to the item before Claude starts. The
cost: palace on its own, outside hill, has no Claude pane, and the pane
can't take the focus from the mouse, as nothing in it watches the mouse;
Pierre chose this over keeping the ACP chat as a fallback, since two
clients are twice the upkeep. micro-claude stays on ACP: inside micro it
has no other way in.

**2026-10-06 · Only work items get marks.** A note's status, its
`since:` and its decisions count only when it is a work item, a
numbered note in a `work/` folder, as `~/projects/CLAUDE.md` defines
one. cognate's pattern notes carry `status: open | partial | covered` in
their frontmatter, and showed as open items. Reading `status:` from
frontmatter only wasn't enough to tell them apart, since theirs is in
frontmatter too, and `open` is in both vocabularies. Nor was a shape
such as `status:` with `since:`, which another schema could take on.
The path is how `./scan` finds items too. Renaming cognate's public field
to suit palace would be the wrong way round (work item 027).

**2026-10-06 · A note Claude selects moves the preview, not Claude.**
When a session writes a note's path to `$PALACE_SELECT`, palace selects
it and the preview follows, but Claude's pane stays on the session that
asked. Following it, as before, parked that session out of sight, where
a permission prompt waited until someone found it, and showed the item's
own session waiting for Ret: nothing moved until you noticed. Starting
the item's session on its own instead would run two conversations at
once on one hand-off; one conversation per item, started by your hand
(selecting it, or `:work`), keeps who you talk to your choice (work item
029).

**2026-10-06 · A done item offers no session.** On an item done and
pushed, Claude's pane says to reopen it (status: doing) or file a new
one, and starts no `claude`. It used to offer to carry the session on,
but palace wraps an idle session on such an item, and a `claude` that
has just started is idle: each start was wrapped some ten seconds
later, before the first question got in. Keeping a session started by
hand on a done item was the other way; Pierre chose the text, since the
wrap already does what `~/projects/CLAUDE.md` says, a follow-up is a new
item or reopens this one, and only the pane said otherwise (work item
030).

**2026-10-06 · How Claude's sessions stand belongs to the hill.** The
file for each session, its `$PALACE_SELECT` file, the session last
shown and the settings palace gives Claude live in hill's folder for the
session (`$HILL_RUN`), not in palace's state folder. Two palaces ran on
one machine, each in its own hill with its own tmux, and shared those
files: one took the other's selections, and when one restarted, it
forgot the other's sessions, whose panes it couldn't find on its own
tmux, so the other ended the `claude` it showed, mid-turn, and offered
it again (17:17:44 in both hills' event logs). The files describe panes
that end with their hill, so hill's folder, which hill removes, is
where they belong. Each project's last session stays in palace's state
folder, shared, as the items' `session:` is (work item 034).

**2026-10-06 · Space opens the key tree, with letters that mean
something.** The commands that had only a name, `set-status`, `status`
with a filter, `work`, `sync`, `scan` and `restart`, get short sequences
in hill's key tree (hill work item 012), so the menu that shows them
teaches the keys. Letters, not ExNovo's digits: palace has the whole
keyboard, and a letter that names its status (`g` go, `f` finished, `h`
hole) is easier to keep than a code. The status letters are the same
under `t` (set) and `f` (show only), so they're learnt once. The commands
that already have a key stay out of the tree, and `:` stays as it is.
Space was the list's second way to open a folder, which Ret already is.

**2026-10-07 · `z` zooms on the projects selected in this palace, and
names say which count as one.** "Worked on" is what you selected notes
in, as the instance's label already counts it, not a file's last change
or a Claude session: a sync or a runner on another machine moves files without
you, and a project read but not talked about still counts. It's the hill
session's own, kept in `$HILL_RUN`, not the instance's or palace's
state folder: zooming is on what you're doing now, so another hill's
work, or yesterday's, mustn't come in (Pierre, after a new hill zoomed
on the projects another had last been in). Where palace starts doesn't
count either, as it's where the last session left off. Projects count as
one by a rule on their names (the same but for the last `-part`, or one
plus `-something`), not a list in the settings, so a new project needs
nothing kept up to date. The zoom holds the projects as they were when
you pressed `z`, so selecting one doesn't reorder the list under the
cursor. `z` has a key and is in the key tree too, under `v` view, as
Pierre asked: the exception to the rule above (work item 037).

**2026-10-07 · palace is hill on PyPI, and brings hill-ops.** Published,
palace takes the name `hill` and the strip becomes `hill-ops` (hill's work
items 014 and 015), so `uv tool install hill` gives the notes screen as
`hill`, with `palace` kept as a second name. mdvault is `hill-mdvault`, as
`mdvault` is taken; the imports don't change. hill-ops is now a
dependency, not an optional program found on PATH: `uv tool install` puts
only palace's commands there, so palace starts hill as `python -m hill`
from its own environment, and `,` outside hill runs its settings the same
way.

**2026-10-07 · sync and scan only where the scripts are.** What palace
reads from `~/projects`'s `sync` and `scan` scripts, the ⏳ 🤍 ⚠ marks,
the Overview's lines from them and `:sync` and `:scan`, stays in the
published palace, offered only for a folder that has the scripts at its
top. It's the same code for Pierre's folder and anyone else's, and
someone who writes scripts of those names gets the same (work item 040,
rather than taking them out).

**2026-10-07 · palace asks for its folder on the first start.** Someone
else's notes aren't in `~/projects`, so palace asks once, in the terminal
before hill starts, with `~/projects` as the answer to Ret where there is
one (so Pierre's start is one key), and keeps it in its settings.json. A
plain question in the terminal rather than in hill's strip: it comes
before hill, and works with `PALACE_NO_HILL` too (work item 040).

**2026-10-08 · A first start with nothing to show makes `~/notes`.**
With no `~/projects`, Ret on the first start's question makes `~/notes`
from a starter folder that ships in the package: a home note, a note of
keys and a made-up project with a sample work item, so the list, the
map, the preview and Claude's pane have something to show. A folder in
the home rather than in palace's own state, since the notes are the
newcomer's to change and keep; one already called `~/notes` is taken as
it is, never written over (hill-ops work item 016).

**2026-10-08 · The strip is hill-ops in palace's words too.** palace's
docstrings, notifications, help and READMEs call the strip it runs in
hill-ops, as its folder, repo and import now are (hill-ops work item
018); text in this file before this entry says "hill" for it. Next, palace
itself becomes hill in its folder, repo and import, as it already is on
PyPI and as its command; `PALACE_*`, `~/.config/palace` and the profile
name `palace` stay.

**2026-10-08 · A cold session starts anew on its item's file.** Claude
Code writes these sessions to the 1-hour prompt cache; a reply after it
expires re-writes the whole context, about 280k tokens on the Mac's
sessions, at twice an input token's price. A new session on the item
reads the system prompt, the CLAUDE.mds and the item, about a seventh
of that. So Claude's pane resumes a work item's session only while its
cache is warm, or when its context is under 60k tokens, and otherwise
starts a new one on the item's file, which the work-item rules already
make the hand-off; `r` resumes the old one, since the conversation is
sometimes worth the price. palace reads the cache's age and size from
the transcript's last reply rather than the hooks, so that a session
from a runner run or the other machine is judged the same way (work
item 049).

**2026-10-08 · An idle session writes its item back before its cache
goes cold.** The rules have a conversation write its item back before
it ends, but an end never comes when you walk away: the state stays in
the session's context, and resuming it once its hour-long cache has gone
costs about ten times a fresh start. So at 20 minutes idle on a `doing`
item, palace types the write-back into the session, a short turn on a
warm cache. 20 minutes rather than 50: silent that long, you came back
within the hour only 39% of the time. Typed, rather than asked by the
Stop hook at the end of every turn, which would add a turn to each
exchange, most answered within minutes. Never while its pane has the
focus, nor twice for one question, nor when the item was committed since
(work item 051).

**2026-10-08 · A session parks once its item hands off.** An idle,
written-back session parks (its `claude` stops, its `session:` stays)
when its item turns `ready` or `running` while it runs, since the next
step is then your `go:` or a job's, not the conversation's. One on a
`waiting` item, on an item already `ready` or `running` when you came to
talk, or on a project parks after Palace → Park idle sessions after,
50 minutes by default. Not 15: an idle session costs nothing, and what
costs is answering it once its hour-long prompt cache has gone, so
parking earlier only throws a warm cache away, while you answer half of
your questions within 4 minutes and 89% within the hour. "Written back"
is the item's file committed, checked with `git status` on that one
path. A session that starts on an item already ready isn't parked at
once, as a done item's once was (work item 030): you came to it to talk
(work item 048).

**2026-10-08 · The timer shows when a session's cache goes cold.** What a
reply costs depends less on how long you've been away than on whether the
session's prompt cache still holds: warm, it's a tenth of the context; cold,
the whole conversation again. So the ✦ timer turns orange in the cache's
last quarter hour and shows ❄ once it's gone, read from the last reply's
usage in the transcript (5 minutes in overage), and the Overview puts the
sessions soonest to go cold first. The orange window pays: silent 30
minutes, you replied within the hour's end only a quarter of the time,
so a nudge then saves a cold start (work item 050).

**2026-10-08 · No session on a mirror's notes.** A mirror is a project
worked on one machine and kept on the other to be read. A session on its
item there writes a `session:` line no one can resume, and could edit what
sync resets to upstream; so the pane offers none, and says where the
project is worked on, rather than run `claude` over ssh, whose hooks would
write their state on the other machine, out of palace's sight. palace learns
the mirrors from sync's state file, which already reads them from the
project table, rather than from a setting of its own that would copy it;
without the file it has none, so it still stands alone (work items 040, 042).

**2026-10-08 · palace's folder, repo and import are hill.** As on PyPI
and as its command (hill-ops work item 018). palace stays its name in
prose, as the app that says hello and as a second command, so what you
read agrees with what the strip shows (`palace · applies right away`).
It starts itself inside hill-ops through its `palace` command
(`palace _loop`) rather than `python -m hill _loop`: hill-ops names a
program started as `python -m X` by X, so the import's new name would
have made every instance from before a different program's, and palace
would have started over in a new one.

**2026-10-08 · The reference is generated; the README explains.** palace's
keys, commands and settings were listed by hand in the README, beside
`hill.toml`, `COMMANDS` and `HELP` that already said them to hill-ops, and
the tests checked one copy against the other. Now `docs/reference.md` is
generated from what palace declares, by hill-ops's `document()` (`python -m
hill.reference`), and committed; a test fails when it's out of date or
something in it is undocumented. The README keeps its prose, what each
part is for and how the parts work together, and links the reference for
the lists, as hill-ops's README does. The reference gives every command,
those that need a folder's sync or scan script too (work item 028).

**2026-10-09 · A mod in palace's Claude sessions, kept thin.** Each
session palace starts loads hill's own Claude Code mod (`src/hill/mod`,
`claude --plugin-dir`), which does from inside the session what palace
could only do from outside it or ask by rule: it shows the item above the
prompt, refuses `git add -A` / `commit -a` (one swept a `session:` line
into a commit with the wrong message, 2026-10-07), and selects a note in
palace with `/select` or its select tool, in place of `echo path >
$PALACE_SELECT`. The mod's API is early access and changes between
builds, so palace's Python side stays authoritative: the mod writes the
same select file, the hooks of `claude --settings` still say how each
session stands, and no hook of the mod refuses what it failed to judge,
so a broken mod loses only these extras. It costs TypeScript beside the
Python, with its own tests, which `uv run pytest` runs through `claude
plugin test` (work item 055).
