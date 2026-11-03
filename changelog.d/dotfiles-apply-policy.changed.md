**Breaking (users):** `untaped dotfiles apply` with no selector follows each
entry's policy: `sync` places what is new or changed and takes back what
the source dropped, `once` places a file once and leaves it, and `manual`
places only what was just turned on; the rest waits for a person, and
`apply` then exits 3 (a timer's `SuccessExitStatus=3`). Naming packages or
items, or the new `--all`, places them whatever their policy. A `link` is a
link only under `sync`: under `once` or `manual` it is a copy, and a link
whose item stops following `sync` becomes a copy of what it shows before
the clone moves. Two new states: `new` (the author added the entry after
the item was first applied here) and `off` (placed for a package or item
that is off now). The preview shows a diff wherever existing content
changes, and a new file's size; each `dotfiles.apply_outcome` row carries
the whole diff in a new `diff` field. `apply` and `add` take `--var` and
`--vars-file`: a value answers every package in the run that declares the
name and is kept in `config.yml`, and an input with no answer is asked at a
terminal. `status --check` exits 3 when there is something for `apply` or
a person to do, and `status.json` gains `apply`, `new` and `off` counts.
