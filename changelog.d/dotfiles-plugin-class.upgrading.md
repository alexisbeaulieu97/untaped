A script that ran `untaped dotfiles status --summary` runs `untaped
dotfiles summary`. One that matches `dotfiles.status` or
`dotfiles.status.summary` kinds matches `dotfiles.path_status` and
`dotfiles.status_summary`. One that reads `dotfiles unsubscribe`'s JSON reads
one object, not a list. One that runs `apply --dry-run` or `remove --dry-run`
as a preview expects exit 1 when a row is planned `conflict` or `failed`. A
machine subscribes and enables its items again under 12.0, which starts from
a clean home.
