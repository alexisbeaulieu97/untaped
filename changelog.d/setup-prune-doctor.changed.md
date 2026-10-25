`untaped doctor`'s `references` row now warns about the reference of an
install that is not reachable, with `setup prune` as its fix. A new
`plugin-folders` row warns about the `plugins/<name>/` folder of a plugin not
installed here that no live reference names, with the same fix, and lists
the folders that stay with why. The `caches` row no longer notes such a
folder's `cached/`.
