Annotate a terminal parameter `Terminal` and pass `self.ctx.terminal` down to
code below the command that prompts (awx's suite variables take it this way),
instead of calling `ui_context()` there. A confirmation is the `Confirm`
block's `self.confirm(question, preview=...)`. A multi-select is `pick_many`.
What a command printed with `ui.styled(...)` or `print_tree(...)`, an
`Output` renderer now returns (below).
