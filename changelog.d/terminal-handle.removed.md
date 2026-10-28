**Breaking (plugins):** `untaped.sdk.UiContext` and `ui_context()` are gone.
The terminal a command asks through is `self.ctx.terminal`, typed
`untaped.sdk.Terminal`, a type with no constructor: the run makes it. It
keeps the prompts (`confirm`, `text`, `secret`, `select`), `pick_many`, `run`,
`require_screen_terminal` and the `terminal(refusal=)` guard. Its stdout
methods (`collection`, `detail`, `styled`), `confirm_action`,
`confirm_or_cancel` and `multiselect` are gone, and so is `print_tree`.
`multiselect` also left the `PromptBackend` protocol, with
`ScriptedPromptBackend(multiselects=)`, `ScriptedTerminal.multiselect` and
`untaped.screen.prompts.multiselect_screen`. `tree_text` draws the same text
whatever the theme: `tree_text(graph, ascii_guides=True)` replaces
`ui=` with an ASCII theme.
