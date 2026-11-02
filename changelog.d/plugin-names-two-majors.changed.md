Names plugins use (the SDK, `untaped.core.api`, each plugin's `api` and
`testing` modules, contracts and record kind names) now last two majors after
their deprecation, so a plugin built on 12.x keeps working on 13.x;
`assert_surface(Plugin, version=...)` holds a plugin's own names to the same
rules and drops a line from its snapshot once it is due, instead of asking you
to delete the file.
