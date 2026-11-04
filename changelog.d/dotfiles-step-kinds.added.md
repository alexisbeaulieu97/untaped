A dotfiles step can use a kind another plugin provides (`kind:
macos.defaults`, its fields under `with:`), which that plugin adds by filling
the experimental `StepKind` contract from `untaped_dotfiles.api` with a record
of its own and which dotfiles asks only for a step this machine approved.
