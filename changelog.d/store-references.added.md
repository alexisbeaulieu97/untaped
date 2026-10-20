Installs of different untaped versions that share `~/.untaped` now clean up
after each other. `untaped setup register` records an install and the store
layouts it uses in `~/.untaped/refs/` (`--remove` takes it back); a registered
install then removes, after a complete refresh, every other layout no
registered install holds and no running untaped has open, with an
`untaped.pruned` info line for each that `--quiet` keeps. `untaped setup plan`
lists registration as its first step (`--check` does not count it), and the
`untaped setup` screen offers it as its first row.
`untaped doctor` gains a `references` row listing every registered install,
and its `stores` row now says what holds each layout and why one is kept.
`untaped setup migrate-dirs` offers what the cleanup leaves: the layouts
nobody holds, as `ansible.index-layouts`, and the references of installs that
are gone, as `untaped.references`. `config migrate` and doctor's unknown-keys
row name the other registered installs that may still read the keys. For
plugins, experimental: `Store(keep=)` for the layouts a setting points at,
`holders()` with its `RefTarget` and `RefHolder`, `other_layouts_migration()`,
and `untaped.testing.write_reference`. How it works is in
[configuration.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/configuration.md#several-versions-on-one-machine).
