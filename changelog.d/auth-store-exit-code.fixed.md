`untaped auth login` exits 4 (the environment needs fixing) instead of 5
(retry later) when no token store, or the one `--store` names, is usable on
this machine, or when gpg can't run for the `pass` store.
