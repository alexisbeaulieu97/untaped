`untaped recipe packs add` and `sync` fetch a git pack through the git
plugin's repo store, so a private pack installs with the credentials a host's
plugin supplies and untaped's proxy; `packs remove` releases the store
repository once no installed pack uses its URL.
