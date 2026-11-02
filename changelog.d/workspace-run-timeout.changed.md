A `workspace run` command that times out gets SIGTERM, then SIGKILL two
seconds later, so it can clean up; it used to get SIGKILL at once.
