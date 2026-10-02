# Policies, states and what each command does

## States of a placed path

| State | Meaning |
|---|---|
| `pending` | Enabled, never applied, target absent. |
| `foreign` | Enabled, never applied, a local file is in the way. Applying keeps it aside under `dotfiles.kept_dir`. |
| `applied` | Target is what the tool wrote and the source has not changed. |
| `behind` | Source changed since the last apply; target untouched. |
| `modified` | Target differs from what the tool wrote (a local edit, or a link replaced by a file); source unchanged. |
| `conflict` | Both `behind` and `modified`. |
| `missing` | Applied before, target now gone. |
| `orphan` | Applied before, the manifest no longer places it (a file left a directory source, or the entry was dropped). |
| `excluded` | Filtered out by `os`, `only`/`unless` or a machine `--skip`; `status --all` shows it. |

A `link` file is `behind` when the fetched ref changed its source and the
clone has not been pulled yet; in a registered checkout it is never
`behind`.

## What `sync` does

`sync` fetches every repo, fast-forwards the managed clones it may, then
applies this table to every enabled path:

| Policy | `pending`/`foreign` | `behind` | `modified` | `conflict` | `missing` | `orphan` |
|---|---|---|---|---|---|---|
| `sync` | apply | apply | report | report | re-apply | remove (an edited copy is kept aside) |
| `once` | apply | skip | skip | skip | report | report |
| `manual` | report | report | report | report | report | report |

`sync` only writes a target that is absent, foreign (kept aside first) or
still exactly what the tool wrote, so it never loses a local edit and never
prompts. It exits 3 when any row is reported, and 1 when an apply or a git
operation failed. It also rewrites `status.json` and `attention` under
`dotfiles.state_dir`; `attention` is the one-line count a prompt reads.

## When a clone is pulled

- `sync` fast-forwards a managed clone only when every enabled `link` file
  reading from it has policy `sync` and the working tree is clean. One
  `manual` link file holds the whole clone back; `sync` then reports
  those link files as `behind` and names them on stderr.
- `apply ITEM` fast-forwards the clone when the item has a `link` file in
  it. Every other link file on that clone moves too, `manual` ones
  included; the plan lists them as `moves with the clone` before the
  confirmation.
- A dirty working tree refuses the fast-forward; the repo row fails with a
  hint to commit or discard the changes.
- A registered checkout (`subscribe PATH`) is fetched, never pulled.

## What `apply` does

`apply` places every selected path whatever its policy: `pending`,
`foreign`, `behind` and `missing` are applied, `orphan` is removed, and
`modified` or `conflict` is refused (row action `conflict`, exit 1) unless
`--force`, which keeps the local version aside. `apply --dry-run` shows the
plan as rows; `--diff` shows a unified diff of `copy` and `merge` changes
before the confirmation.

## What `remove` does

`remove ITEM` deletes the paths the tool placed, keeps a `modified` or
`conflict` copy aside instead of deleting it, takes merged keys back out of
the target document, and disables the item. `disable ITEM` leaves every
file where it is.
