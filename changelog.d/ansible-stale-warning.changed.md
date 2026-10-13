**Breaking (ansible):** a refresh that fails over cached data serves it with
one `untaped.stale` warning, which replaces the old "source data is stale" and
"refresh of NAME had N failures" warnings, and an empty answer from a source
that could not be refreshed exits with the refresh's error.
