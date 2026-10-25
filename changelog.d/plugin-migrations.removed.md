**Breaking (users):** `setup migrate-dirs` no longer moves or deletes what
10.x and 11.0 left behind: ansible's 10.x index and git caches, dotfiles'
10.x directory, github's sweep cache, 9.x corpus and inventory file,
recipe's 10.x library and workspace's 10.x clones and 9.x `repositories`.
`untaped_git.api` drops `adopt`, `Adopted`, `bare_repos`, `overlaps_store`,
`remove_if_emptied`, `repo_origin` and `unfinished_removals`, which only
those moves used.
