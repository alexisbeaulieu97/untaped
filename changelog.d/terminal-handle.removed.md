**Breaking (plugins):** `untaped.sdk.UiContext` and `ui_context()` are gone.
The terminal a command asks through is `self.ctx.terminal`, typed
`untaped.sdk.Terminal`; the run makes it, so a plugin never builds one. It
keeps the prompts (`confirm`, `text`, `secret`, `select`), `pick_many`, `run`,
`require_screen_terminal` and the `terminal(refusal=)` guard. Its stdout
methods (`collection`, `detail`, `styled`), `confirm_action`,
`confirm_or_cancel` and `multiselect` are gone, and so is `print_tree`.
