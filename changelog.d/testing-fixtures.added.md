The testing kit gains the `terminal` fixture (a `ScriptedTerminal`, absent
until a test scripts it: `terminal.answers(yes=True)`,
`terminal.types("Ada")`, `terminal.screens(...)`), the `credentials` fixture
(the `MemoryCredentials` every test runs with:
`credentials.set(Hello.token, key, "secret")`), `check(install(Hello))`
(every failing row of `untaped plugin check` at once) and
`assert_surface(Hello)` (a plugin class, with the snapshot at
`snapshots/surface.txt` beside the test unless `path=` says otherwise).
