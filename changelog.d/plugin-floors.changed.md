**Breaking (plugins):** plugins declare floors on `untaped` and on other
plugins (`untaped>=12.0`, `untaped-git>=12.1`), never an upper limit or an
exact pin, so any number of plugins install together and keep working across
releases. `lint.requirement-floor` replaces `lint.provides-requirement`; it
fails an upper limit or a pin, except between packages built from one uv
workspace. `untaped plugin new` scaffolds floors.
