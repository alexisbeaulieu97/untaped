`untaped dotfiles apply` clones an item's `repos:` entries: a working copy
whose target is absent is cloned once through the git plugin, with its
credentials and proxy and nothing left in the copy's config, and then left
to the person. A checkout of the entry's URL already there is `applied`, and
anything else there is `foreign` and never touched. `status` lists them as
`kind: repo` rows, and `remove` and `unsubscribe` forget them and leave the
copies in place.
