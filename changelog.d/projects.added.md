`untaped project init` makes any folder a project: its `.untaped/` holds
`project.json`, and the home records it by the folder's filesystem identity,
so a moved project is followed and a copy gets its own id. `untaped project
list` shows each recorded project and whether its folder is present, missing
or unreadable, and `untaped doctor`'s `references` row (retitled from
`install references`) lists projects and warns about a missing one. For
plugins, experimental: `Project`, `current_project()`, `project_at()` and
`committed_path()` in `untaped.sdk`, which names a plugin's own committed
files under `.untaped/`, and `untaped.testing.make_project`. How it works:
[projects.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/projects.md).
