An entry point can name a 12.0 plugin class (`hello =
"untaped_hello.plugin:Hello"`): untaped composes it, builds its command line
from the declaration (options, arguments, bundles, groups, panels, marks and
renamed spellings), validates what was typed as the command class (a value
the model refuses exits 2 naming its flag), and prints what `run` returns.
In `run`, `self.output` reads the chosen `format` and the table's `columns`
(`.add(...)` adds more, `.empty(text)` sets what an empty table shows), and
`self.stdin` reads `identifiers(...)`, `values(...)` or `rows(...)` from the
arguments or `--stdin`, refusing rows of a kind `Stdin[R]` does not accept
(`.requested` says `--stdin` was passed, before anything is read).
A plugin class that composition refuses is quarantined (`bad-plugin-class`)
with every problem it names; arguments that cannot be filled in field order
(one after a list argument, a required one after an optional one) are one of
those problems (`positional-order`), and so is an option spelled as a root
option: `--profile`, `--verbose`/`-v`, `--quiet`/`-q` or `--deprecated`
(`root-option`).
