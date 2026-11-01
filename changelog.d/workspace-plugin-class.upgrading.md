A script that matches the `workspace.status` kind matches
`workspace.repo_status`. A worktree's git uses the profile the folder's
project picks: `untaped --profile NAME workspace create ...` records it, and
`UNTAPED_PROFILE` in the shell still stacks on it. A script that ran `cd
"$(untaped -q workspace create NAME ...)"` creates first, then runs `cd
"$(untaped workspace path NAME)"`.
