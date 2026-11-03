A dotfiles repo's `dotfiles.yml` becomes one file per package under
`.untaped/dotfiles/`, each starting `version: 1`, with `source:` spelled
`path:` (the dotfiles README shows one). A machine subscribes and adds its
packages again under 12.0, which starts from a clean home. `dotfiles sync`
is gone: `dotfiles apply --yes` places every package that is on, whatever
its policy, so a timer that only reports runs `dotfiles status --check`. A
prompt that read `dotfiles summary` reads `status.json`. A script that
matched the `dotfiles.item`, `dotfiles.path_status` or
`dotfiles.apply_outcome` rows reads `dotfiles.package`,
`dotfiles.entry_status` and the new `dotfiles.apply_outcome` keys
(`source`, `package`, `item`, `entry`).
