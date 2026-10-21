`untaped.sdk.file_lock` is re-entrant within a thread: taking a lock the same
thread already holds no longer waits for itself and fails as busy. Writes to
`config.yml` and `state.yml` (`StateCollection`, `StateMap`, config writes)
are the exception: one started inside another write to the same file fails at
once instead of saving over it.
