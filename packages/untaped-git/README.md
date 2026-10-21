# untaped-git

Install it as part of `untaped`: `uv tool install 'untaped[git]'` or `pip install 'untaped[git]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped git` is where untaped reaches Git remotes with its own credentials.
Plugins that know a Git host (GitHub, for one) supply credentials and a proxy
for it; the repo store keeps one shared bare repository per remote, which
other plugins fetch into and add worktrees from. `untaped git` itself has no
setup: it reports what the other plugins supply.

## Set up

The repo store needs Git 2.29 or newer; `untaped doctor` checks the version.
The store lives under `~/.untaped/plugins/git/store` unless you set
`git.store_dir` (see the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#git)).

## Use

See which plugin answers for each Git host, and whether a credential is
available:

```bash
untaped git hosts
```

See what the repo store holds, which plugins use each repository, and what
only one plugin uses (what releasing it would free):

```bash
untaped git store
```

A plugin's own delete or remove command releases a repository: its refs,
file and worktrees go, and the repository itself goes once no other plugin,
hand-added worktree, branch or stash holds it.

In the worktrees untaped creates, Git asks `untaped git credential` for the
host's credentials, after your own credential helpers. When an old keychain
entry answers first, set `git.untaped_helper_only: true`: untaped's worktrees
then ask only untaped for that host, never your own helpers.

## Reference

For agents, `untaped agents git` prints these commands, the two settings and which plugin answers for each Git host, in Markdown, and each command's own page (`untaped agents git store`, for one) its options. Install the skill that points an agent at it with `untaped skills install git --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)).

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#git) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#git)
