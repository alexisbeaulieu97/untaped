`untaped.sdk.file_lock` is re-entrant within a thread: taking a lock the same
thread already holds no longer waits for itself and fails as busy.
