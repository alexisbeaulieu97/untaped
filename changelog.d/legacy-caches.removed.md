A registered install no longer removes 11.0's `plugins/<name>/caches/`
folders after a fill, and no `untaped.pruned` line without a store or layout
is written: `store` and `layout` are always set. Delete a `caches/` folder
11.0 left by hand.
