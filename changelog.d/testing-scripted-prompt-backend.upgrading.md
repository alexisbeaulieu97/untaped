A test that built `ScriptedPromptBackend(confirms=[True], texts=["Ada"])`
scripts the `terminal` fixture instead, or builds
`ScriptedTerminal().answers(True).types("Ada")`: `secrets=` and `texts=` are
one `types(...)`, in the order the command asks them, `selections=` is
`selects(...)`, `picks=` and `screens=` are `picks(...)` and `screens(...)`,
`interrupt=True` is `interrupts()`, and a backend with nothing scripted is
`present()`. Read `backend.asked` where it read `backend.calls`. A test that
called `assert_destructive_contract` runs the command twice: once piped
without `--yes` (it exits 2 and says it `requires --yes`) and once after
`terminal.answers(False)` (it asks, then changes nothing), or runs a built
command with `install(Hello).run(command, answer=False)`.
