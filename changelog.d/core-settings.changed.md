**Breaking (users):** untaped's own settings moved into its `untaped`
section, as any plugin's sit in its own: `http.*`, `ui.*` and `skills.*` are
now `untaped.http.*`, `untaped.ui.*` and `untaped.skills.*` in a profile, and
their variables `UNTAPED_UNTAPED__HTTP__*`, `UNTAPED_UNTAPED__UI__*` and
`UNTAPED_UNTAPED__SKILLS__*`. The `shell` section is gone, `setup` shows one
`untaped` section, and `http`, `ui`, `skills` and `shell` are free as plugin
names.
