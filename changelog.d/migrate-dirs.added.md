`untaped setup migrate-dirs` moves or deletes the directories older versions
left, with a preview, `--yes` and `--dry-run`: workspace's and github's 10.x
caches move into the repo store without downloading anything, the 9.x and
ansible caches are deleted, and `~/.untaped/repositories` stays until
`--dissociate` repacks the clones that borrow from it. A directory that holds
the repo store or untaped's own files, or that two rows claim, is never
touched. `untaped doctor` warns while anything is left.
