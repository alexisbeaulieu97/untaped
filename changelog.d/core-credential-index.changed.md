**Breaking (users):** the index of `auth login`s lives in core's own
`~/.untaped/plugins/untaped/state.yml`, as its `credentials` rows, instead of
under `credentials.index` in `~/.untaped/state.yml`. Logins filed by 11.x are
no longer read: run `untaped auth login` again for each token you keep. A
damaged row offers `untaped auth forget --id ID`, which forgets the row even
when it can't be read.
