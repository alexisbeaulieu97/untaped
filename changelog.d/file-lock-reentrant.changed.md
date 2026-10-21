`untaped.sdk.file_lock` is re-entrant within a thread: taking a lock the same
thread already holds no longer waits for itself and fails as busy. A write to
`config.yml` or `state.yml` (`StateCollection`, `StateMap`, config writes)
started inside another write to the same file now fails at once instead of
being lost when the outer write saves.
