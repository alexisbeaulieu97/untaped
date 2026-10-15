Upgrade from 10.x in this order. First copy your `shell.aliases` (below) and,
to keep the repo store somewhere other than `~/.untaped/plugins/git/store`,
set `git.store_dir` to a new directory, not an old cache root. Then run
`untaped setup migrate-dirs`: while the old settings still name them, it moves
github's and workspace's repositories (custom cache directories included) into
the store and ansible's, recipe's and dotfiles' data under
`~/.untaped/plugins/`, and deletes the old caches and github's inventory file;
until it runs, ansible starts an empty index and recipe lists no installed
packs. Last, run `untaped config migrate`, which renames old keys and deletes
retired ones, printing the values they held, and rename old `UNTAPED_*`
variables yourself (below).
([#589](https://github.com/alexisbeaulieu97/untaped/pull/589),
[#591](https://github.com/alexisbeaulieu97/untaped/pull/591),
[#592](https://github.com/alexisbeaulieu97/untaped/pull/592),
[#593](https://github.com/alexisbeaulieu97/untaped/pull/593),
[#654](https://github.com/alexisbeaulieu97/untaped/pull/654),
[#657](https://github.com/alexisbeaulieu97/untaped/pull/657))
