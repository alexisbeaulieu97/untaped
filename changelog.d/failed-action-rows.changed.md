An outcome row whose `action` is `failed`, `partial` or `conflict` now sets
exit 1 even without an `error` (`untaped fix` included); a row whose failure
is remembered from an earlier run, a field marked not counted, still does
not.
