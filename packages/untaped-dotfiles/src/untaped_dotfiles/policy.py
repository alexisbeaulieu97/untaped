"""The update policy of a package or item, whose docstring is the guide to entry states."""

from __future__ import annotations

from enum import StrEnum


class Policy(StrEnum):
    """How eagerly a package or item follows its source, and the state of each placed file.

    A package suggests a policy (`policy:` in its file; `manual` when unset);
    this machine chooses its own with `untaped dotfiles add SELECTOR --policy`,
    for a whole package or one item. The row's `policy` is the machine's
    choice when it made one (`chosen: true`), else the author's.

    ## States of a file entry

    - `pending`: on, never placed here, target absent.
    - `foreign`: on, never placed here, a file untaped didn't write is in the
      way. Placing it keeps that file aside under `dotfiles.kept_dir`.
    - `applied`: the target is what untaped wrote and the source is unchanged.
    - `behind`: the source changed since untaped placed it; the target is
      untouched. A `link` file is `behind` when the fetched commit changed its
      source and the clone hasn't moved yet; in a local folder it never is.
    - `modified`: the target differs from what untaped wrote (a local edit, or
      a link replaced by a file); the source is unchanged.
    - `conflict`: both `behind` and `modified`.
    - `missing`: placed before, the target is gone.
    - `orphan`: placed before, the package no longer places it (a file left a
      folder entry, or the entry was dropped).
    - `excluded`: left out by `os`, `only` or `unless` on this machine.
    - `error`: could not be read (a git or filesystem failure, or an input
      with no value); the row's `error` says why.

    ## What `apply` does

    `apply` fetches the git sources of the selected packages and moves each
    clone to the fetched commit, then places every selected entry whatever its
    policy: `pending`, `foreign`, `behind` and `missing` entries are placed,
    `orphan` ones taken back, and `modified` or `conflict` ones refused unless
    `--force` (the local version is kept aside). A local folder is read as it
    stands, never fetched or moved.

    ## What needs a person

    `status --check` exits 3 when an entry needs a person: `behind`,
    `modified`, `conflict`, `missing` or `orphan` (under `once`, only
    `missing` and `orphan`, since `once` means "apply, then leave alone"). It
    exits 1 when an entry could not be read (a row carrying an `error`).
    """

    SYNC = "sync"
    """New versions apply as they arrive."""
    ONCE = "once"
    """Apply, then leave alone."""
    MANUAL = "manual"
    """Report; apply when asked."""
