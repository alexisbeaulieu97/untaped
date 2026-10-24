**Breaking (users):** a run stacks profiles: `UNTAPED_PROFILE` and
`--profile` take comma lists (and `--profile` repeats), layered over the base
in order, the variable's names first and the later name winning.
`--profile default` reads the base alone and sets the variable aside;
`default` inside a longer list, a malformed name or an unknown name exits 2
(unknown names list the known ones in `details.known`). Writes land in the one
profile `--profile` names, else the base, never in what `UNTAPED_PROFILE`
picks; `--profile a,b` on a write exits 2 with `details.profiles`. `profile
current` prints the pick comma-joined (or `default`) and its `--format json`
is `{name, profiles: [{name, source}]}`; `profile list` and `profile show`
say `selected` instead of `active`; provenance labels the base `base`.
Profile names are letters, digits, `_`, `.` and `-`, not starting with `.` or
`-`.
