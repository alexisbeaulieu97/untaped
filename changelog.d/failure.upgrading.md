Replace `ErrorInfo` with `Failure`, and `error=note_failure(exc, message=m)`
with `error=Failure.from_exception(exc, message=m)`; emit the row and its
failure counts. ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
