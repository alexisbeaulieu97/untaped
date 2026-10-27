A plugin whose table showed a `*_s` or `elapsed` field as a run time marks it
`Annotated[float, Duration]`, and one whose `sha`, `commit`, `revision` or
`scm_revision` column was shortened marks it `Annotated[str, ObjectName]`; a
command that emits plain dicts emits a `Record` with those fields to keep the
formatting.
