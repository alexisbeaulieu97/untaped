A custom `untaped.testing.PromptBackend` may define
`run_screen(screen, *, theme)` to run screens (`ui.run`); the method is
optional, so a backend written for 10.0 still type-checks, and `ui.run` on
one without it fails naming the method. A backend may set
`needs_terminal = False`, as `untaped.testing.ScriptedPromptBackend` does,
to run screens and `pick_many` without a terminal (a prompt still needs a
TTY stdin).
([#525](https://github.com/alexisbeaulieu97/untaped/pull/525))
