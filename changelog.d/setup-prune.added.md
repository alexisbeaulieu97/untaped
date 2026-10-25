`untaped setup prune` removes what unreachable installs and uninstalled
plugins left in the home: the references of installs that are not reachable
(deleted without `setup register --remove`, or on a disk not mounted now),
and the `plugins/<name>/` folders of plugins not installed here, each whole,
with its data, caches and state. It lists both, then asks once (`--dry-run`
previews, `--yes` skips the question, and off a terminal without `--yes` it
exits 2). A folder stays, as a `kept` row, while a live reference names its
plugin (another install sharing the home composes it, or a project holds its
items), while any reference cannot be read, while a live install's reference
doesn't list its plugins, or when it is a symlink. Each item is checked again
under the references' lock before it goes; one that can't be removed fails
the run while the others still go, and a busy lock fails them all (exit 5,
retry later). It writes `untaped.prune_outcome` rows.
