**Breaking (users):** `untaped lint` is now built on a 12.0 plugin class; its
flags, record kinds and exit codes keep their meaning. A `lint.rule` row's
`id` pattern takes the public `<key>.<id>` that `lint rules` prints, so `lint
rules -f json` reads back as rules. A table of findings shows each finding's
target on every row, and its location as `src/a.py:3`. A rule a repository's
lint file loads with a dotted id is dropped as a malformed rule (exit 1),
since the run prefixes the key. A hint reads run `untaped lint rules`,
spelled from the installed commands. The guides are at `untaped docs lint
guides findings` and `guides rule-module`, and the lint file reference at
`untaped docs lint inputs lint_file`.
