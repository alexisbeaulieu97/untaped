Two ansible index refreshes at once wait for each other instead of failing
with `database is locked`, and an index that stays busy is a retryable error
(exit 5). The index uses SQLite's WAL mode, so keep `ansible.index_dir` on a
local disk.
