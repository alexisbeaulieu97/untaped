`untaped setup prune` removes what installs and plugins that are gone left in
the home: the references of installs deleted without `setup register
--remove`, and the `plugins/<name>/` folders of plugins not installed here,
with their data, caches and state. It lists both, then asks once
(`--dry-run` previews, `--yes` skips the question, and off a terminal without
`--yes` it exits 2). A symlinked folder, and one whose plugin's data a
project or another install still holds, stays as a `kept` row; each item is
checked again before it goes, and one that can't be removed fails the run
(exit 1) while the others still go. It writes `untaped.prune_outcome` rows.
`untaped doctor`'s `references` row now warns about the reference of an
install that is gone, and a new `plugin-folders` row about the folder of a
plugin not installed here, both with `setup prune` as their fix; the `caches`
row no longer notes that folder's `cached/`.
