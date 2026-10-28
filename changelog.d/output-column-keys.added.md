`Output(columns=...)` takes field references, `key(Repo).full_name`, so a
renamed or missing field fails where the command is declared. A reference to
a field of another model than the command's record is refused, and so is a
nested one.
