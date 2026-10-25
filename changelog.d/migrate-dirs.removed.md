**Breaking (users):** `untaped setup migrate-dirs` and doctor's
`migrate-dirs` row are gone, with every row it ran: core's (the references
of installs that are gone, an uninstalled plugin's `cached/` folder) and the
plugins' (10.x's and 11.0's folders: ansible's index and git caches,
dotfiles' directory, github's sweep cache, corpus and inventory, recipe's
library, workspace's clones and `repositories`), and the store layouts the
automatic cleanup keeps; doctor's `stores` row still names each.
