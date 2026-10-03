# untaped-ansible

Install it as part of `untaped`: `uv tool install 'untaped[ansible]'` or `pip install 'untaped[ansible]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped ansible` answers questions about how Ansible roles and projects
depend on each other through their requirements files: what a role depends
on, what depends on it, which projects reach a repository, and the whole
graph. It is for anyone about to change a shared role.

Downstream works from live GitHub reads. Upstream needs a *source*: a saved
set of orgs, teams or repos whose dependency files `untaped` scans and caches.

## Set up

`untaped ansible` reads GitHub through the `github` settings, so set a GitHub
token first. Then save a source, refresh it once, and make it the default so
you can leave out `--source`:

```bash
untaped auth set github
untaped ansible source set platform --org acme --team acme/platform
untaped ansible source refresh platform
untaped config set ansible.default_source platform
```

Private repos also need Git access over HTTPS with that token, or over SSH.
Scanned files, clone protocol, cache locations and refresh tuning are in the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#ansible).

## Show what a role depends on

```bash
untaped ansible deps acme/base-role
untaped ansible deps acme/base-role --ref v2.1.0 --depth 2
untaped ansible deps ./roles/web --target-repo acme/web --format json
```

One row per repo the role reaches, with the file that declares it and the
shortest path. With a source selected, `deps` reads its cache; `--live` reads
GitHub instead.

## Find what depends on a role

```bash
untaped ansible source status
untaped ansible impact acme/base-role --refresh
untaped ansible impact acme/base-role --org acme --refresh
```

`impact` lists the repos in the source that depend on the role, at any ref,
from cached data, and marks where the walk stopped. Refresh first when the source is stale; inline selectors such as
`--org` answer a one-off question without saving a source.

## Find which roots contain a repository

```bash
untaped ansible find acme/base-role --root acme/site@main --root acme/app
untaped awx job-templates list --organization Default --with-scm --format pipe \
  | untaped ansible find acme/base-role --stdin --format pipe
```

One row per ref of the repository that each root reaches, with the shortest
path. Rows
carry the input record's id and name, so the results join back to AWX job
templates or any other piped records.

## Draw the graph

```bash
untaped ansible graph acme/base-role
untaped ansible graph acme/base-role --direction down --format mermaid --out deps.mmd
untaped ansible graph acme/base-role --direction up --depth 2 --format json
```

A tree of what depends on the target and what it depends on, a Mermaid
diagram, or a JSON graph. Without a source it shows only the downstream half.

## Manage sources

```bash
untaped ansible source patch platform --add-repo acme/legacy-app
untaped ansible source-alias set acme.base acme/ansible-role-base
untaped ansible source refresh platform
untaped ansible source remove platform --dry-run
```

Patch a source rather than replacing it, and map Galaxy role names to their
repos with source aliases. A patch drops the source's cached data, and an
alias applies only at the next scan, so run `source refresh` after either.
`source remove` and `source-alias remove` preview and ask first.

## Reference

The [packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-ansible/src/untaped_ansible/skills/untaped-ansible/SKILL.md) is the full reference: every workflow, safety rule and pitfall. Install it for your agent with `untaped skills install ansible --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)); `untaped ansible COMMAND --help` lists each command's options.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#ansible) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#ansible)
- [GitHub](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-github/README.md)
