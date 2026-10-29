A command line untaped can't parse is said as its other errors are
(lowercase, no closing period): `command not found: 'nosuch'; known: …`,
`unexpected argument: 'b'`, `config get: KEY requires an argument`,
`unknown option: --columsn; did you mean --columns?`. A stray token after
`--` is `unexpected argument after --: '--dry-run'`; it read `Unknown
option: --dry-run. Did you mean --dry-run?`. The exit stays 2.
