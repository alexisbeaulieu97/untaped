# untaped-ansible

Install it as part of `untaped`: `uv tool install 'untaped[ansible]'` or `pip install 'untaped[ansible]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped ansible` answers questions about how Ansible roles and projects
depend on each other through their requirements files: what a role depends
on, what depends on it, which projects reach a repository, and the whole
graph. It is for anyone about to change a shared role.

Downstream reads each repo live from the plugin that lists it. Upstream
needs a *source*: a saved selection of the repos a plugin lists, whose
dependency files `untaped` scans and caches.

## Set up

Repos come from the installed plugins that list them (those filling the git
plugin's `RepoSource`; `untaped plugin list` shows each plugin's `fills`), so
install and set up one of them first. Then save a source, refresh it once,
and make it the default so you can leave out `--source`:

```bash
untaped ansible source set platform --from PLUGIN --match 'acme/*'
untaped ansible source refresh platform
untaped config set ansible.default_source platform
```

`--from PLUGIN` takes the repos that plugin lists, and `--match` keeps those
whose full name matches. A plugin with its own way to select repos (by team,
say) prints a selector row with one of its commands; pipe it to
`source set NAME --stdin` instead. Each refresh asks the source's plugin for
its repos again, at the URLs it gives, with the credentials of the plugin
serving each host. The repos live in the git plugin's repo store
(`git.store_dir`). Scanned files, the index location and refresh tuning are in the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#ansible).

## Show what a role depends on

```bash
untaped ansible deps acme/base-role
untaped ansible deps acme/base-role --ref v2.1.0 --depth 2
untaped ansible deps ./roles/web --target-repo acme/web --format json
```

One row per repo the role reaches, with the file that declares it and the
shortest path. With a source selected, `deps` reads its cache; without one, it
reads each repo live where its plugin lists it, through the repo store, which
fetches a ref again only when it moved.

## Find what depends on a role

```bash
untaped ansible impact acme/base-role
untaped ansible impact acme/base-role --source platform
untaped ansible impact acme/base-role --cached
```

`impact` lists the repos in the source that depend on the role, at any ref,
from cached data, and marks where the walk stopped. A source older than
`ansible.caches.source_data.max_age_seconds` (one day) is refreshed first;
`--cached` reads only what is cached.

## Find which dependents use a variable

```bash
untaped ansible impact uses acme/base-role base_port
```

One row per place a dependent defines, sets or reads `base_port`, with the
file, line and YAML key path, read at the commit the source indexed. A
refresh analyses each repo's YAML files and templates once per file
version, beside its dependency files.

## Find what checks out the dependents

```bash
untaped ansible impact checkouts acme/base-role
```

One row per checkout of a dependent, from every installed plugin that fills
ansible's `CheckoutSource` contract (with its job templates, say). Each
names the dependent it checks out at its ref, or none when the source
doesn't hold that ref. A plugin that wasn't asked gets a hint, and one that
failed is an error with its own exit code; their answers are cached for
`ansible.caches.checkout_source_checkouts.max_age_seconds` (one hour), and
`--refresh`/`--cached` reach them too.

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
untaped ansible source patch platform --add-path meta/main.yml
untaped ansible source-alias set acme.base acme/ansible-role-base
untaped ansible source refresh platform
untaped ansible source remove platform --dry-run
```

Patch what a source scans rather than replacing it (pipe a new selector to
`source set` to change its repos), and map Galaxy role names to their repos
with source aliases. A patch drops the source's cached data, and an
alias applies only at the next scan, so run `source refresh` after either.
`source remove` and `source-alias remove` preview and ask first. Removing a
source also frees the repos only it used from the repo store; a repo another
saved source or plugin still uses stays.

## Reference

`untaped docs ansible` explains how a walk resolves refs and lists every command with its options, the settings and the guides; the untaped you run generates it.

`untaped skills install ansible --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)) points your agent at it.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#ansible) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#ansible)
- [Git](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-git/README.md): the repo store, and the contracts plugins fill to list repos and serve hosts
