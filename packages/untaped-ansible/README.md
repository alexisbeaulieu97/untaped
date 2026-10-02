# untaped-ansible

Install it as part of `untaped`: `uv tool install 'untaped[ansible]'` or `pip install 'untaped[ansible]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped ansible` answers questions about how Ansible roles and projects
depend on each other through their requirements files:

| Command | Question | Output |
|---|---|---|
| `deps ROLE` | What does ROLE depend on (downstream)? | one `ansible.dependency` row per repo reached, per ROLE ref |
| `impact ROLE` | What depends on ROLE (upstream)? | one `ansible.dependent` row per repo reached, per ROLE ref |
| `find REPO --root ROOT` / `--stdin` | Which roots contain REPO downstream? | one `ansible.dependency_match` row per match |
| `graph TARGET` | The whole picture, both directions | a tree, Mermaid diagram or JSON graph |

Downstream works from live GitHub reads. Upstream needs a *source*: a saved
set of orgs, teams or repos whose dependency files `untaped` scans and caches.

The packaged skill is the full reference:
[sources and refreshes](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-ansible/src/untaped_ansible/skills/untaped-ansible/references/sources.md)
and [graph resolution and output](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-ansible/src/untaped_ansible/skills/untaped-ansible/references/graphs.md).

## Set up

`untaped ansible` reads GitHub through the `github` settings, so set a GitHub
token first (see [GitHub](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-github/README.md#set-up)):

```bash
untaped config set github.token --prompt
```

Private repos also need Git access over HTTPS with that token, or over SSH
when `ansible.git_clone_protocol` is `ssh`.

Save a source, refresh it once, and make it the default so you can leave out
`--source`:

```bash
untaped ansible source set platform --org acme --team acme/platform
untaped ansible source refresh platform
untaped config set ansible.default_source platform
```

`ansible.default_source` is used by `deps`, `impact`, `find` and `graph`
whenever you give neither `--source` nor an inline selector. With a source
selected, every command reads the cached data and touches GitHub only with
`--refresh`; `deps`, `find` and `graph` also take `--live` to read downstream
dependencies from GitHub anyway.

Scanned files, clone protocol, cache locations and refresh tuning are in the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#ansible).

## Show what a role depends on

```bash
untaped ansible deps acme/base-role
untaped ansible deps acme/base-role --ref v2.1.0 --depth 2
untaped ansible deps ./roles/web --target-repo acme/web --format json
```

`ROLE` is `owner/repo`, a GitHub URL, a source alias, or a local path (for a
subdirectory of a checkout, add `--target-repo OWNER/NAME`).

Without a source, `deps` reads GitHub live. With one, it reads the source's
cache: a role outside the source gets a "not cached" warning and no rows.
Pass `--live` to read GitHub anyway. The same holds for `find` and for
`graph`'s downstream half. Collections in requirements files are not
followed; the command warns once and lists them.

## Find what depends on a role

```bash
untaped ansible impact acme/base-role
untaped ansible impact acme/base-role --source platform --refresh
untaped ansible impact acme/base-role --ref main --format pipe
```

`impact` reads cached source data only: from `--source NAME` (repeat to
combine), from inline selectors, or from `ansible.default_source`. Pass
`--refresh` (or run `source refresh`) when you want current data. For a
one-off question, use inline selectors instead of a saved source; the scan is
cached, so repeating the same command reuses it:

```bash
untaped ansible impact acme/base-role --org acme --refresh
```

## Find which roots contain a repository

`find REPO` walks the downstream graph of each root and prints one row per
node of REPO it reaches, and nothing for roots that never reach it. Give
roots with `--root` (repeatable) or through `--stdin`, and one or more repos
to look for:

```bash
untaped ansible find acme/base-role --root acme/site@main --root acme/app
printf 'acme/site@main\nacme/app@v2.1.0\nacme/tools\n' \
  | untaped ansible find acme/base-role --stdin
untaped awx job-templates list --organization Default --with-scm --format pipe \
  | untaped ansible find acme/base-role --stdin --format pipe
```

A root is `owner/repo@ref` (the ref is optional), a Git URL, a source alias,
or a local path. Stdin carries either such lines or `--format pipe` records
naming a repository and ref, such as AWX job templates.

Each `ansible.dependency_match` row names the root, the matched repo, the
dependency file and the shortest path from the root, plus the input
record's kind, `id` and `name`. Every input record gets its own rows, so a
pipeline can join the results back to its inputs.

## Draw the graph

```bash
untaped ansible graph acme/base-role
untaped ansible graph acme/base-role --direction down --format mermaid --out deps.mmd
untaped ansible graph acme/base-role --direction up --depth 2 --format json
```

`graph` shows both directions by default; `--direction up` shows only what
depends on the target and `--direction down` only what it depends on.
Without a source it shows only downstream and warns that upstream was
omitted. The tree looks like this:

```text
acme/base-role@main  source platform · unlimited depth

used by
└── acme/site@main                roles/requirements.yml · unpinned

depends on
├── acme/legacy@v1                meta/main.yml
│   └── acme/shared@main [1]      meta/main.yml · unpinned
│       └── acme/leaf@main        requirements.yml
└── acme/users@v1.2.0             requirements.yml
    └── acme/shared@main see [1]  requirements.yml · unpinned

5 repos · 6 edges
```

The first line names the target, where the data came from and the depth.
After each repo comes the file that declares that dependency, plus
`unpinned` when it names no version, or `pins X` when the declared version
differs from the ref it resolved to. A repo marked `…` with a `not read:`
note is stopped: its own dependencies (or dependents) were not read, because
`--depth` ran out (`depth`) or its ref is not in the source's cache
(`not_cached`), so the graph beyond it may be incomplete. The
[graph reference](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-ansible/src/untaped_ansible/skills/untaped-ansible/references/graphs.md#tree-output) lists every marker.

## Flags

`deps`, `impact`, `find` and `graph` share their options:

- `--source NAME` (repeatable) picks saved sources; inline `--org`, `--team`
  and `--repo` selectors describe a one-off source instead. Without either,
  `ansible.default_source` is used.
- `--refresh` refreshes the source first; `--live` reads downstream from
  GitHub even with a source (`impact` has no `--live`).
- `--depth N` limits the walk; the default is `unlimited`.
- `--ref REF` picks the target's branch, tag or SHA; without it, what the
  target depends on is read at its default branch, and `--all-refs` reads
  every cached ref instead. What depends on a target always covers all of
  its refs, since a repo pinning an older tag still uses it.

A table shortens each `path` to its first and last hop (`a → … → z`);
other formats keep it whole.

## Manage sources

```bash
untaped ansible source status
untaped ansible source patch platform --add-repo acme/legacy-app --remove-team acme/platform
untaped ansible source remove platform --dry-run
```

`source set NAME` creates or replaces a source from `--org`, `--team` and
`--repo`; `source patch NAME` edits it with `--add-*`, `--remove-*` and
`--clear-*`. `source status` shows whether each source is `fresh`, `stale`
or `not_refreshed`.

`source refresh` saves each repo that succeeds. When some repos fail it
lists them and exits non-zero; run it again to retry only those. A large
refresh that runs low on GitHub API budget stops early, exits 5 and resumes
where it left off when you run it again. Backends, rate-limit fallbacks and
tuning are in the
[sources reference](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-ansible/src/untaped_ansible/skills/untaped-ansible/references/sources.md#refreshing).

## Source aliases

When a requirements file names a role by Galaxy name rather than a Git URL,
map it to its repo:

```bash
untaped ansible source-alias set acme.base acme/ansible-role-base
untaped ansible source refresh platform
```

Source aliases apply when a source is refreshed, which is why the refresh
follows.

## Read the output

- `stopped` on a row or node (`depth` or `not_cached`) means the next level
  beyond that repo was not read: its dependencies for `deps`, its
  dependents for `impact`. Treat the answer as incomplete there.
- Each repo appears once per root ref, at its shortest path; `root_ref`
  says which ROLE ref a row came from.
- A dependency at `repo@v1` and one at `repo@main` are different nodes; an
  unpinned dependency points at the dependency's default-branch node.
- A cycle is found only within the depth you asked for.
- A malformed or templated dependency file is skipped with a warning; it
  never fails the command.

Every row field, the tree markers and the JSON graph shape are in the
[graph reference](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-ansible/src/untaped_ansible/skills/untaped-ansible/references/graphs.md).

## Troubleshooting

- **"no cached source data found"**: the source was never refreshed. Run the
  `source refresh` command the error prints, or add `--refresh`.
- **An older index is rebuilt**: after an upgrade the SQLite cache
  (`ansible.index_path`) may be rebuilt empty with a warning. Refresh each
  source again.

## Output

See [Scripting](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#ansible) and
[Exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#exit-codes).

## See also

- [Configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#ansible)
- [GitHub](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-github/README.md)
