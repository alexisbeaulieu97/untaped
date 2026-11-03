"""The update policy of a package or item, whose docstring is the guide to entry states."""

from __future__ import annotations

from enum import StrEnum


class Policy(StrEnum):
    """How eagerly a package or item follows its source, and what `apply` does in each state.

    A package suggests a policy (`policy:` in its file; `manual` when unset);
    this machine chooses its own with `untaped dotfiles add SELECTOR --policy`,
    for a whole package or one item. The row's `policy` is the machine's
    choice when it made one (`chosen: true`), else the author's.

    A `link` file is placed as a link only under `sync`; under `once` or
    `manual` it is placed as a copy, since a link would change the moment
    the clone moves. When a link's item stops following `sync`, `apply`
    makes it a copy of what it shows before the clone moves, so nothing a
    program reads changes; when a copy's item starts following `sync`, it
    is `behind` and becomes a link.

    ## States of a file entry

    - `pending`: on, never placed here (or just asked for again: turned on,
      or an input its target names answered), target absent.
    - `foreign`: the same, with a file untaped didn't write in the way.
      Placing it keeps that file aside under `dotfiles.kept_dir`.
    - `new`: the author added it after the item was first applied here.
    - `applied`: the target is what untaped wrote and the source is unchanged.
    - `behind`: the source changed since untaped placed it; the target is
      untouched. A `link` file is `behind` when the fetched commit changed its
      source and the clone hasn't moved yet; in a local folder it never is.
    - `modified`: the target differs from what untaped wrote (a local edit, or
      a link replaced by a file); the source is unchanged.
    - `conflict`: both `behind` and `modified`.
    - `missing`: placed before, the target is gone.
    - `orphan`: placed before, the package no longer places it: a file left a
      folder entry, the entry or its item was dropped, the author changed its
      target, an input moved its target, or the package file left the source.
    - `off`: placed before, and its package or item is off now (turned off in
      `config.yml` by hand); `untaped dotfiles remove` takes it back. What a
      source deleted from `config.yml` by hand placed is no longer listed:
      subscribe it again to take it back.
    - `excluded`: left out by `os`, `only` or `unless` on this machine. A
      file placed before it was left out stays as it is.
    - `error`: could not be read (a git or filesystem failure); the row's
      `error` says why. A package file that doesn't load, a source that can't
      be opened, or a target two packages place, is reported on stderr and
      counts as an `error` in the status files; a disputed target keeps what
      was placed there until one package lets it go.

    An entry whose target names an input with no value waits for one (a
    person answers it: `apply` reports it), and what it placed before stays
    as it is.

    ## What `apply` does

    `apply` fetches every git source and moves each clone to the fetched
    commit (a clone with local changes stays where it is, and its source's
    changes fail with a hint). With no selector each entry follows its
    policy:

    | State | `sync` | `once` | `manual` |
    |---|---|---|---|
    | `pending`, `foreign` | place | place | place |
    | `new` | place | place | report |
    | `behind` | place | - | report |
    | `modified`, `conflict` | report | - | report |
    | `missing` | place again | report | report |
    | `orphan` | take back | report | report |
    | `off` | report | report | report |

    "report" leaves the entry for a person and makes `apply` exit 3; `once`
    skips quietly, since it means "apply, then leave alone". An `orphan`
    whose target an input moved (the package declares the same target) is
    taken back whatever the policy, in the run that places the new target:
    this machine asked for it. A target the author changed is an `orphan`
    and a `new` entry like any other.

    `apply SELECTOR...` and `apply --all` place what they name whatever its
    policy: `pending`, `foreign`, `new`, `behind` and `missing` entries are
    placed, `orphan` ones taken back, and `modified` or `conflict` ones
    refused unless `--force` (the local version is kept aside).

    ## What needs a person

    `status --check` exits 3 when there is something for `apply` or a person
    to do: an entry `apply` would change on its own, or one it reports. It
    exits 1 when an entry could not be read (a row carrying an `error`), or a
    package or source could not be. `status.json` counts both: `apply` and
    `attention`; the `attention` file holds the second, for a prompt.
    """

    SYNC = "sync"
    """New versions apply as they arrive."""
    ONCE = "once"
    """Apply, then leave alone."""
    MANUAL = "manual"
    """Report; apply when asked."""
