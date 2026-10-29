`untaped config set` names a refused key once (`invalid value for
'awx.page_size': Input should be greater than 0`). In `config get`, `set` and
`unset`, an unknown key reads `setting not found: 'awx.nope'; known: …` with
the section's keys (or the sections) instead of every key on one line, and
it, like a retired or deleted key, is category `not_found` (exit 1, as
before) with `untaped config migrate` as its hint where one helps.
