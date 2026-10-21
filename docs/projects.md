# Projects

A project is a folder untaped knows: its `.untaped/` folder holds
`project.json`. Any folder can be one, except `~` and `~/.untaped` themselves.
Projects are [experimental](./versioning.md#experimental).

```console
$ cd ~/work/PROJ-123 && untaped project init
✓ ~/work/PROJ-123 is a project (id 3f9a1c2b7d10)
$ untaped project init
info: ~/work/PROJ-123 is already a project
$ untaped project list
name       path              state    current
PROJ-123   ~/work/PROJ-123   present  True
```

## What `init` writes

`untaped project init [PATH]` makes the folder (the current one by default)
a project:

- `.untaped/project.json` holds its id, twelve hex digits chosen at random.
  It is created only if it doesn't exist, so two `init`s at once make one
  project.
- `.untaped/.gitignore` keeps `project.json`, `state.yml`, `plugins/` and
  lock files out of git, and itself when `init` creates it. A repository can
  share its own `.untaped/` files, such as `lint.yml`, never a project's id.
- The home records the project in `~/.untaped/refs/project-<id>.json`: its
  folder and the folder's filesystem identity.

Projects don't nest: `init` refuses inside another project, and in a folder
that holds one this home records. Run again in a project, `init` changes
nothing, or brings the home's record up to date (below).

## Moves and copies

untaped tells "the same folder" by its filesystem identity, never by its path:
a second spelling, a symlink or a bind mount is the same project.

- **Moved** on one filesystem (`mv`): the folder keeps its identity. Commands
  that only read say it moved; the next command that changes the project
  makes the record follow it, and `untaped project move OLD NEW` does it now.
- **Copied** (`cp -r`): the copy has the original's id but not its identity.
  Commands run there say so, and the next command that changes it (such as
  `untaped project init` there) gives it its own id and no holds; the
  original keeps its own.
- **Can't tell** (moved to another filesystem, or copied while the original's
  drive is away): untaped refuses to guess (exit 4). Say which it was:
  `untaped project move OLD NEW` if it moved, `untaped project init --new-id`
  in a copy.

Nothing is repaired by a command that only reads, such as `project list`, or
under `--dry-run`.

`project list` shows each recorded project's state: `present`, `missing`
(its folder is gone) or `unreadable` (its drive or share isn't there now; it
keeps what it holds). `untaped doctor` warns about a missing project.

## Move, remove and prune

```console
$ untaped project move ~/work/PROJ-200 ~/archive/PROJ-200
plugin     item                 action    detail
untaped    ~/archive/PROJ-200   moved     moved the folder; its reference follows
workspace  github.com/acme/api  repaired  repaired worktree api
$ untaped project remove ~/work/PROJ-123 --dry-run
plugin     item                 action   detail
workspace  github.com/acme/api  planned  removes worktree api; keeps the repo: project PROJ-200 holds it
workspace  github.com/acme/web  refused  branch PROJ-123: 2 commits never pushed
untaped    ~/work/PROJ-123      planned  deletes .gitignore, project.json, state.yml; keeps config.yml (yours)
```

- `project move OLD NEW` moves the folder on one filesystem; untaped never
  copies a project, so across filesystems `mv` it yourself and run
  `project move OLD NEW` after. With OLD gone it makes the record follow
  NEW, and adopts NEW (after a confirmation) when it holds no
  `project.json` any more. Each plugin the project holds items of then
  repairs what points into the folder; a repair that fails warns and is
  retried by the next command that changes the project.
- `project remove [NAME|PATH]` (the current project by default) asks each
  plugin how the project lets go of what it holds, shows the plan, and
  applies nothing if one hold is refused (exit 1); `--force ITEM` overrides
  that item's refusal, and forgets a hold whose plugin isn't installed (never
  one whose plugin is broken: fix it instead). Then
  it deletes only what untaped wrote: `project.json`, `state.yml`,
  `plugins/`, its own lock files and an untouched `.gitignore`. `config.yml` and
  your own files stay, and so does the folder unless nothing else is in it.
- `project prune` does the same for every project whose folder is gone, and
  skips the ones it can't read now.

All three preview with `--dry-run`; `remove` and `prune` exit 3 under
`--check` when a hold is refused, and confirm (`--yes` without a terminal).

## A plugin's files in `.untaped/`

Every name in `.untaped/` is core's (`project.json`, `config.yml`,
`state.yml`, `plugins/`, `.gitignore`) or one plugin's. A plugin names the
files people write, and may commit, through `committed_path` in
`untaped.sdk`:

```python
from untaped.sdk import checkout_root, committed_path

lint_file = committed_path(SPEC, "lint.yml", root=checkout_root(start) or start)
```

The name is the plugin's own: its name (`awx`), `<plugin>.<ext>`
(`lint.yml`), or a path under `<plugin>/` (`lint/house.py`); anything else is
a `ValueError`. `project_at(path)` and `current_project()` find the project a
folder is in (`current_project()` is a usage error, exit 2, outside one).

`nearest_pyproject(start)` walks up to the farther of the checkout root and
the project root, as `uv` finds a project from a worktree inside it.

## Holding a plugin's items

A plugin that keeps items for projects (workspace keeps their repos) is
their owner: it declares an `ItemStore` on `PluginSpec.stores` and says how
a project lets go of an item, and whether a moved project needs repair.

```python
from untaped.sdk import ItemStore, LetGo, MoveAsk, Refused, ReleaseAsk


def _release(ask: ReleaseAsk[RepoKey]) -> LetGo[RepoKey] | Refused:
    from acme.application.held import plan_release  # noqa: PLC0415

    return plan_release(ask)


REPOS = ItemStore("acme", "repos", item=RepoKey, release=_release, moved=None)
```

`project.hold(REPOS, key)` records a hold before the work that needs it, and
`project.holds(REPOS)` lists them. `release` gets a `ReleaseAsk` with the
item, the project and every other holder, and returns a `Refused` (a reason
and a hint) or a `LetGo`: a `preview` line and an `apply` that runs after
confirmation on a fresh ask, under the store's lock. `apply` may do less
than the preview (another holder arrived), never more: when the fresh ask
needs more, it returns a `Refused` and the hold stays. `moved` gets a
`MoveAsk` naming where the project was (`moved_from`), or is `None` when
nothing outside the folder points into it. `project.release(REPOS)` runs
the same steps for the plugin's own commands. `untaped.testing` has
`make_project(path, holds=...)`, `release_ask`, `move_ask` and
`plan_release` for unit tests.
