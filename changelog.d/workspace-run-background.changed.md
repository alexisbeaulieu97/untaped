Background processes a `workspace run` command starts keep running after it
exits, unless they still hold its output: those are stopped two seconds after
it exits. All of them used to be stopped, except ones in their own session.
