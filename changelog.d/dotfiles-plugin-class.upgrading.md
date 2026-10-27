A script that ran `untaped dotfiles status --summary` runs `untaped
dotfiles summary`. One that matches `dotfiles.status` or
`dotfiles.status.summary` kinds matches `dotfiles.path_status` and
`dotfiles.status_summary`. One that reads `dotfiles unsubscribe`'s JSON reads
one object, not a list. A machine subscribes and enables its items again under
12.0, which starts from a clean home.
