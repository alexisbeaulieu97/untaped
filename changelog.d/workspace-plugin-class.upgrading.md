A script that matches the `workspace.status` kind matches
`workspace.repo_status`. To keep a worktree on a profile, give its project a
`profile:` line or set `UNTAPED_PROFILE` in the shell that runs git there. A
script that ran `cd "$(untaped -q workspace create NAME ...)"` creates first,
then runs `cd "$(untaped workspace path NAME)"`.
