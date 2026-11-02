A SIGTERM or SIGHUP (`kill`, `timeout`, a closed terminal, a cancelled CI job)
runs the same cleanup as Ctrl-C, such as cancelling the AWX jobs a test run
launched, and stops untaped's child processes before untaped ends by that
signal; it used to end untaped on the spot.
