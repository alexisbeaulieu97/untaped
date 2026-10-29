`untaped dotfiles apply`, `sync` and `remove` show each row's `target_path` in
tables, and the plan they ask about is the same table of planned rows, laid
out as every other untaped preview; it names each target by its absolute
path, where it used to write `~`. A row's `error` now comes after its
`target_path` in JSON.
