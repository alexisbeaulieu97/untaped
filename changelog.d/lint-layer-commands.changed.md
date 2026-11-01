**Breaking (plugin authors):** `untaped lint`'s `layer` rule checks what
importing a plugin's light modules loads: `api.py`, `settings.py` and
`records.py`, and the package `__init__` they run, never import `commands/`,
directly or through another module (imports inside a function or an `if
TYPE_CHECKING:` body don't count). It no longer checks a `cli → application →
domain` direction: the plugin's own logic may import its commands. The
`settings` rule still flags settings resolved in `domain/`, `application/` or
`infrastructure/`. Both rules' findings now carry the import's line.
