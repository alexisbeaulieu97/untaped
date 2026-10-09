`PromptBackend` gains `run_screen`; a custom backend needs the method to
type-check (it may set `needs_terminal = False`, as `ScriptedPromptBackend`
does, to run screens and prompts without a terminal).
([#525](https://github.com/alexisbeaulieu97/untaped/pull/525))
