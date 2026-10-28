`Output(columns=...)` takes field references, `key(Repo).full_name`, so a
renamed or missing field fails where the command is declared. A nested field
is its dotted column, `key(CaseResult).failure.message` for
`failure.message`. A reference to a field of a model other than the command's
record (or a base it inherits) is refused.
