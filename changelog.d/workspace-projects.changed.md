**Breaking (users):** a workspace is the git part of a project.
`workspace create NAME` makes `<workspaces_dir>/NAME` a project (as
`untaped project init` does, recording the run's `--profile` pick) and keeps
the workspace's rows in its `.untaped/`; `workspace add` works in any project.
The project holds each repo it checks out, so `untaped project remove`
releases them, `project move` reconnects the worktrees, and git refuses to
prune them (they are locked). `archive` removes the worktrees and keeps the
folder and its rows; `create NAME` on an archived workspace resumes it,
checking its repos out again. `remove NAME` lets go of the repos through the
holds service (one refusal changes nothing), then drops the workspace's rows
and the project when workspace made it. NAME is a workspace's name or its
folder; two active workspaces with one name exit 2. `list` and
`status --all` show the projects that are here now. `workspace remove` rows
are `planned`, `refused`, `released`, `removed`, `kept` or `failed`, and lose
`freed_bytes`.
