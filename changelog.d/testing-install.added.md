`untaped.sdk.testing.install` composes plugin classes into the test's home
with their settings as models: `install(Hello, settings=HelloSettings(...))`
then `.run(Greet())` runs a built command and `.cli("hello say ada")` a
command line, both returning one `Ran` (`exit`, `stdout`, `stderr`,
`rows_as(Model)`). The pytest plugin gains the `install`, `home` and `clock`
fixtures.
