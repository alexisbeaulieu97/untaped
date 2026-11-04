A script passing `--live` to `untaped ansible deps`, `find` or `graph` drops
it and selects no source: run it where `ansible.default_source` is unset, such
as in a profile without it. An install of `untaped[ansible]` alone adds a
plugin that lists repos, such as `untaped[github]`.
