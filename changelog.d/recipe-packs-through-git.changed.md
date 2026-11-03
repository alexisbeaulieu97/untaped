`untaped recipe packs add` and `sync` fetch a git pack through the git
plugin's repo store, so a private pack installs with the credentials a host's
plugin supplies and untaped's proxy; a commit `--rev` must be one the
remote's branches or tags reach, the pack is written exactly as committed,
and `packs remove` releases the store repository once no installed pack uses
its URL.
