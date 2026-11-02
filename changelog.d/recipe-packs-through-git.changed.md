`untaped recipe packs add` and `sync` fetch a git pack through the git
plugin's repo store, so a private pack installs with the credentials a host's
plugin supplies and untaped's proxy, a `--rev` the remote has as no branch,
tag or reachable commit exits 3, and `packs remove` releases the store
repository once no installed pack uses its URL.
