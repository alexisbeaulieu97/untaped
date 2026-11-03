`untaped dotfiles` merges TOML files: a `merge` file whose target ends in
`.toml`, or that says `format: toml`, writes the keys its source names into
the target and keeps the target's other keys, comments and order. A new table
is written inline where a `[header]` would capture the keys after it, and a
merge that can't be written without changing other keys writes nothing.
