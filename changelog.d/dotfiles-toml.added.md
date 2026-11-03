`untaped dotfiles` merges TOML files: a `merge` file whose target ends in
`.toml`, or that says `format: toml`, writes the keys its source names into
the target and keeps the target's other keys, comments and order. A new table
under dotted keys is written inline, and a merge that would move other keys
is refused, writing nothing.
