**Breaking (users):** a recipe input's `sensitive:` is now `secret:`, with no
old spelling, and `recipe get` rows name the field `secret`. A `list` input
given as text (`--var`, `--vars-file` or a `default`) also takes
comma-separated text (`--var cols=name,owner`), a missing required `list` is
asked at a terminal as comma-separated text, and a run missing several
required inputs names them all at once. Some messages are reworded: a path
field refusing a secret reads `secret input 'x' cannot be used in field
'dest'`, a structured value a template can't hold reads `structured input 'x'
cannot be rendered into text`, and an unknown input error, from `--var` or
`--input-from`, names every unknown input and the declared ones and exits 2
(usage), as does an `--input-from` naming a global input or whose expression
doesn't compile (`invalid --input-from expression for NAME`). `--var`,
`--vars-file` and `--non-interactive` list under their own help panel, which
says `NAME=VALUE` and links `untaped docs values guides vars`.
