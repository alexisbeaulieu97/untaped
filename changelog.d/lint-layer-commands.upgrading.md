**Plugin authors:** where `untaped lint` now reports `layer` on `api.py`,
`settings.py` or `records.py`, move what that module needs from `commands/`
into a module of its own that `commands/` imports, or import the command
inside the function that uses it. Findings that named `cli` imports are gone:
a plugin's own logic may import its commands.
