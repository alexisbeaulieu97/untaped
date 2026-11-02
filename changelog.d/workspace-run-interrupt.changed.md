Ctrl-C during `workspace run` sends the running commands SIGINT, as a
terminal does, instead of SIGTERM, and SIGKILL two seconds later; a command
that cleans up on Ctrl-C now gets to.
