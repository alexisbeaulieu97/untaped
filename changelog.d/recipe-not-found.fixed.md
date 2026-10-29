`untaped recipe` says every missing thing as other lookups do, the name
quoted (`backup not found: 'zzz'; known: …`, `template not found:
'template.txt'`), with the not-found exit: backups, recipe and pack recipe
files, templates and copy sources, transform and content files, packs, pack
sources, hook projects and hook modules. A missing asset in `recipe
validate` was an invalid value. `recipe packs sync` names the command that
fixes a pack without a usable recorded source as its `hint:`, not inside the
error.
