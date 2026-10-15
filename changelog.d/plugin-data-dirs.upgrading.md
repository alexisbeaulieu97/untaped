Run `untaped setup migrate-dirs` before using ansible, recipe or dotfiles:
it moves their data under `~/.untaped/plugins/`, and until it runs ansible
starts an empty index and recipe lists no installed packs. A profile that
sets one of those directory settings keeps its own place. A prompt or status
line reading `~/.untaped/dotfiles/attention` must read
`~/.untaped/plugins/dotfiles/attention`.
