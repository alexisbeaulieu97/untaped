A validator names a command by its class with
``InvalidValue("use `{}`", Login.spelled())``: pydantic reports it as any
`ValueError`, and its text spells the command as the run that reports it
mounts it.
