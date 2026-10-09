If ssh prompted you while `untaped` ran Git, add the key to `ssh-agent` and
the host to `known_hosts` (a plain `git fetch` of that remote asks to trust
it) before running `untaped`.
