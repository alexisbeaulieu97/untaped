Writes to `config.yml` and `state.yml` through a symlink and through the real
file wait for each other: the lock lives in the home's `locks/`, keyed by the
real path, and no `config.yml.lock` is left beside the file.
