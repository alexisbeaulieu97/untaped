"""The policy an enabled item follows, whose docstring is the guide to path states and policies."""

from __future__ import annotations

from enum import StrEnum


class Policy(StrEnum):
    """How eagerly an enabled item follows its repo: what each policy does in each path state.

    ## States of a placed path

    - `pending`: enabled, never applied, target absent.
    - `foreign`: enabled, never applied, a local file is in the way. Applying
      keeps it aside under `dotfiles.kept_dir`.
    - `applied`: the target is what the tool wrote and the source has not changed.
    - `behind`: the source changed since the last apply; the target is untouched.
    - `modified`: the target differs from what the tool wrote (a local edit, or a
      link replaced by a file); the source is unchanged.
    - `conflict`: both `behind` and `modified`.
    - `missing`: applied before, the target is now gone.
    - `orphan`: applied before, the manifest no longer places it (a file left a
      directory source, or the entry was dropped).
    - `excluded`: filtered out by `os`, `only`/`unless` or a machine `--skip`.
    - `error`: could not be read (a git or filesystem failure); the row's
      `error` says why.

    A `link` file is `behind` when the fetched ref changed its source and the
    clone has not been pulled yet; in a registered checkout it is never
    `behind`.

    ## What `sync` does

    `sync` fetches every repo, fast-forwards the managed clones it may, then
    acts on every enabled path by its item's policy:

    - `sync`: applies `pending`, `foreign` and `behind` paths, re-applies
      `missing` ones and removes `orphan` ones (an edited copy is kept aside);
      it reports `modified` and `conflict` paths.
    - `once`: applies `pending` and `foreign` paths and skips `behind`,
      `modified` and `conflict` ones; it reports `missing` and `orphan` paths.
    - `manual`: reports every path.

    `sync` only writes a target that is absent, foreign (kept aside first) or
    still exactly what the tool wrote, so it never loses a local edit and never
    prompts. It exits 3 when a path needs the user: one left `behind`,
    `modified`, `conflict`, `missing` or `orphan` (under `once`, only `missing`
    and `orphan`). A `manual` `pending` or `foreign` path is reported but does
    not count. It exits 1 when an apply or a git operation failed, or a path
    could not be read (a row carrying an `error`). `status --check` exits the
    same way.

    ## When a clone is pulled

    - `sync` fast-forwards a managed clone only when every enabled `link` file
      reading from it has policy `sync` and the working tree is clean. One
      `manual` link file holds the whole clone back; `sync` then reports
      those link files as `behind` and names them on stderr.
    - `apply ITEMS` fetches every repo the item reads from, then fast-forwards
      the clone when the item has a `link` file in it. Every other link file
      on that clone moves too, `manual` ones included; the plan lists them as
      `moves with the clone` before the confirmation.
    - A dirty working tree refuses the fast-forward; the repo row fails with a
      hint to commit or discard the changes.
    - A registered checkout (`subscribe PATH`) is fetched, never pulled.

    ## What `apply` does

    `apply` fetches the repos of the selected items first, so a managed
    clone's manifest, `copy` and `merge` files are read as its remote holds them
    now; a registered checkout is still read from its working tree. It places
    every selected path whatever its policy: `pending`, `foreign`, `behind` and
    `missing` paths are applied, `orphan` ones are removed, and `modified` or
    `conflict` ones are refused unless `--force`.
    """

    SYNC = "sync"
    """New versions apply as they arrive."""
    ONCE = "once"
    """Apply, then leave alone."""
    MANUAL = "manual"
    """Report; apply when asked."""
