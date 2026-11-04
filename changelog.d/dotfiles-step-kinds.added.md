A dotfiles step can use a kind another plugin provides (`kind:
macos.defaults`, its fields under `with:`): the plugin fills the experimental
`StepKind` contract from `untaped_dotfiles.api` with a record of its own,
whose kind names the step kind and whose fields `with:` must match. Dotfiles
asks that plugin alone, only for an approved step. A kind no installed plugin
provides is a `skipped` row naming the plugin to install (exit 3); a provider
core refused says why. `untaped docs dotfiles extensions step-kind` lists
every installed kind's fields, and doctor warns about a provider that adds
no kind.
