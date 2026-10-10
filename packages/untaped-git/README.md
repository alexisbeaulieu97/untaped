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

In the worktrees untaped creates, Git asks `untaped git credential` for the
host's credentials, after your own credential helpers. When an old keychain
entry answers first, set `git.untaped_helper_first: true`: untaped's worktrees
then ask only untaped for that host, never your own helpers.

## Reference

The [packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-git/src/untaped_git/skills/untaped-git/SKILL.md) is the full reference: the commands, the repo store's rules and the pitfalls. Install it for your agent with `untaped skills install git --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)); `untaped git COMMAND --help` lists each command's options.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#git) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#git)
