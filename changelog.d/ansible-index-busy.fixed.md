Two ansible index refreshes at once wait for each other instead of failing
with `database is locked`; an index another process holds for over 60 seconds
is a retryable error (exit 5).
