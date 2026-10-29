**Breaking (users):** each plugin keeps its state in its own
`~/.untaped/plugins/<plugin>/state.yml`; a `state.yml` at the top of the home
was written by 11.x and is refused (exit 4). A project's `.untaped/state.yml`
is no longer core's: `project init` no longer ignores it in git and
`project remove` leaves it.
