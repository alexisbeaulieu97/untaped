If ssh prompted you while `untaped` ran Git, add the key to `ssh-agent` and
the host to `known_hosts` (a plain `git fetch` of that remote asks to trust
it) before running `untaped`. If an HTTPS remote asked for a password through
`core.askPass` or `SSH_ASKPASS`, store it in a Git credential helper or set
`GIT_ASKPASS`.
