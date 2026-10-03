# Graph resolution and output

Contents: rows, refs, repos and local targets, where a walk stops, `find`
input, tree output, graph JSON, skipped files.

## Rows

`deps` rows (`ansible.dependency`) and `impact` rows (`ansible.dependent`)
list their fields with `--columns '?'`. What the fields do not say:

- `declared_ref` and `declared_in` are verbatim from the edge that reached the
  repo; `unresolved` is the declared name of a dependency that names no
  GitHub repo.
- `path` is the shortest path, in dependency order: from ROLE for `deps`,
  towards ROLE for `impact`. `root_ref` is the ROLE ref the row was reached
  from.
- `stopped` is `depth` or `not_cached` when the next level beyond this repo
  was not read, else `null`.
- Each repo appears once per root ref, at its shortest path.
- A table shortens `path` to its first and last hop (`a → … → z`); json,
  yaml, raw and pipe keep it whole.

## Refs

- Without `--ref`, `deps`, `find` and `graph` read what ROLE depends on at
  its default branch. `impact` covers every ref a dependent pins.
- `--all-refs` reads what every cached ref depends on. It needs a source and
  exits 2 with `--live` or `--ref`. Local checkouts and `find` roots given
  with a ref are read as they are.
- When the source has not scanned the default branch (a tags-only source),
  every cached ref is read, with a warning.
- An unpinned dependency resolves to the dependency's default-branch node:
  the source's recorded default branch, or GitHub's for live reads. With no
  known default branch the node stays ref-less; a tags-only source stops
  there with a "ref is not cached" warning.
- `impact --ref R` (and `graph --ref R --direction up`) includes unpinned
  consumers when `R` is the target's cached default branch. If that branch
  is unknown, a warning counts the unpinned dependents omitted.

## Repos and local targets

- Repo ids match case-insensitively (`Acme/Base` equals `acme/base`). Graph
  node ids are lowercase; labels keep the display casing.
- URLs on the host of `github.base_url` resolve like `github.com` URLs.
  Path-like sources such as `./local` stay unresolved.
- A local path resolves its repo from the Git remote only at the top level
  of a checkout (linked worktrees included). For a subdirectory, pass
  `--target-repo OWNER/NAME`.
- Without a source, a local target is read live. With no GitHub token it
  stays offline and warns that transitive dependencies were not expanded.

## Where a walk stops

- `stopped: depth` means `--depth` ran out before the repo's own edges were
  read; `--depth N` also prints one `hint:` on stderr (per root for
  `find`).
- `stopped: not_cached` means the repo's ref is not in the source's cache.
- A live read that fails for one repo (deleted repo or tag, lost access)
  becomes a warning and leaves that node unexpanded. A rejected token and
  rate limits abort the command instead.
- Collections in requirements files are not followed. `source refresh`,
  `--refresh` and local targets print one warning listing them; live GitHub
  reads do not.
- Cycles are found only inside the graph that was walked; use `--depth
  unlimited` (the default) when looking for long loops.

## `find` input and rows

- Roots come from repeatable `--root owner/repo[@ref]` or `--stdin`: bare
  `owner/repo@ref` lines, or pipe records naming a repo and ref (such as
  `untaped awx job-templates list --with-scm --format pipe`). An empty or
  missing ref means the default branch.
- Several `REPO` arguments find any of them.
- Each `ansible.dependency_match` row has `root_repo`, `root_ref`, `repo`,
  `declared_ref`, `declared_in` and the shortest `path`, plus the input
  record's `input_kind`, `input_id` and `input_name` (`null` for `--root`
  and bare lines).
- Every input record gets its own rows even when records share a root;
  identical bare lines collapse.
- A repo reached through two declared refs gives two rows. An unpinned and a
  default-branch-pinned declaration of one repo give one.
- Each root's warnings go to stderr, prefixed with the root.

## Tree output

- The first line names the target, the data source and the depth. A "used
  by" section lists dependents and a "depends on" section dependencies.
- Each repo line names the file that declares the edge, plus `unpinned` (no
  version) or `pins X` (the declared version differs from the resolved ref).
- A shared subtree prints once, numbered `[n]`; later occurrences say
  `see [n]`. A repo already on the path says `↻ cycle` (`(cycle)` with ASCII
  glyphs).
- A stopped repo is marked `…` with a `not read:` note.
- The summary counts repos, edges, cycles, unresolved dependencies and
  stopped repos. A component with too many cycles to list counts as one
  cyclic group.
- Lines too wide for the terminal end in `…`. Warnings go to stderr, never
  into the tree, even with `--out`.

## Graph JSON

- The document has `target_id`, `nodes`, `edges`, `cycles` and
  `warnings`. Nodes carry `stopped` as on rows.
- `edges[].id` is stable and identical for every declaration of one
  dependency (relation, source and target), so duplicate declarations
  collapse to one edge.
- A cycle record has `kind`, `relation`, `node_ids` and `edge_ids`.
  `kind: cycle` is a closed ordered path. `kind: scc_group` is a sorted set
  of nodes and internal edges, used when a component has too many cycles to
  list.

## Skipped dependency files

- Malformed, templated or wrong-shape dependency files are skipped with a
  warning and never fail the command.
- Empty files and missing, null or empty dependency sections are silent. A
  `dependencies`, `roles` or `collections` section that is present but not a
  list warns and is skipped.
- Live warnings name `repo@ref path`. `source refresh` prints the skipped
  files for that run only; they are not stored with the cached graph.
