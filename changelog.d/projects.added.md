`untaped project init` makes any folder a project: its `.untaped/` holds
`project.json`, and the home records it by the folder's filesystem identity,
so a moved project is followed and a copy gets its own id. `untaped project
list` shows each recorded project and whether its folder is present, missing
or unreadable; `project move` moves one or repairs a hand move, and `project
remove` and `project prune` release what a project holds through the plugins
that own it, with a preview, then delete only what untaped wrote.
`untaped doctor`'s `references` row (retitled from `install references`)
lists projects and warns about a missing one. For plugins, experimental:
`Project`, `current_project()`, `project_at()`, `known_projects()` and
`committed_path()` in `untaped.sdk`, which names a plugin's own committed
files under `.untaped/`, and `untaped.testing.make_project`. How it works:
[projects.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/projects.md).
