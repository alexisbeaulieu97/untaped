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
that holds one. Run again in a project, `init` changes nothing, or brings the
home's record up to date (below).

## Moves and copies

untaped tells "the same folder" by its filesystem identity, never by its path:
a second spelling, a symlink or a bind mount is the same project.

- **Moved** on one filesystem (`mv`): the folder keeps its identity. Commands
  run there say it moved, and `untaped project init` there makes the record
  follow it.
- **Copied** (`cp -r`): the copy has the original's id but not its identity.
  Commands run there say so, and `untaped project init` there gives it its
  own id; the original keeps its own.
- **Can't tell** (moved to another filesystem, or copied while the original's
  drive is away): untaped refuses to guess (exit 4). In a copy,
  `untaped project init --new-id` gives it its own id.

Nothing is repaired by a command that only reads, such as `project list`.

`project list` shows each recorded project's state: `present`, `missing`
(its folder is gone) or `unreadable` (its drive or share isn't there now; it
counts as present). `untaped doctor` warns about a missing project.

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
