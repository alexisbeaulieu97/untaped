A pack's hook worker now gets untaped's Ctrl-C, SIGTERM or SIGHUP as it
arrives, with what its hooks started, and SIGKILL two seconds later; before,
it ran in its own session, out of the signal's reach, until untaped closed it.
