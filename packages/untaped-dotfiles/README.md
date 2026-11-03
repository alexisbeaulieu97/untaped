# untaped-dotfiles

Install it as part of `untaped`: `uv tool install 'untaped[dotfiles]'` or `pip install 'untaped[dotfiles]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped dotfiles` places config files on a machine from *packages* that
*sources* offer. A source is a git repo or a local folder; each of its
`.untaped/dotfiles/<name>.yml` files is one package, made of named
*items*, and each item lists *files* with a `mode` (`link`, `copy` or
`merge`). A machine subscribes to sources and turns packages (or single
items) on. The package suggests an update policy; the machine can choose
its own: `sync` (new versions apply as they arrive), `once` (apply, then
leave alone) or `manual` (report; apply when asked).

`dotfiles` is [experimental](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/versioning.md#experimental) and may change in
a minor release.

## A package

```yaml
# .untaped/dotfiles/fish.yml
version: 1
description: Fish shell config
policy: manual                 # the author's suggestion; manual when unset
os: [macos, linux]
items:
  config:
    files:
      - {path: fish/config.fish, target: ~/.config/fish/config.fish}
      - {path: fish/conf.d, target: ~/.config/fish/conf.d}   # each file placed on its own
```

A target may name an input, `{{ name }}`, that the package declares under
`inputs:` (with an optional `default`); the machine answers it in its
config. Every key, the three modes, inputs, per-OS and per-tag filters,
steps and repos, and every rule a package must follow to load, are in the
docstring of the
[package model](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-dotfiles/src/untaped_dotfiles/package.py)
(`untaped plugin schema dotfiles.package_file` prints its schema). A
package that breaks a rule is one row with an `error`; the other packages
still load. Steps and repos load and show, but `apply` places files only
for now.

On Windows, `link` needs symlink permission (Developer Mode or elevation);
`copy` and `merge` work. Nothing is tested there yet.

## Subscribe, add, apply

```bash
untaped dotfiles subscribe https://git.example.com/acme/dotfiles.git --add fish
untaped dotfiles subscribe ~/work-dotfiles --name work
untaped dotfiles show                     # every package the sources offer
untaped dotfiles add starship --policy sync
untaped dotfiles add claude/skills        # one item of a package
untaped dotfiles apply --dry-run
untaped dotfiles apply
```

`subscribe` records the source in `config.yml` (`dotfiles.sources`) and
lists its packages. A git source gets a clone under `dotfiles.clones_dir`
through the git plugin's repo store, so its credentials and proxy apply;
a local folder is read as it stands, never fetched. `add` turns packages
or items on and places nothing; `remove` takes back what they placed and
turns them off (`--keep-files` leaves the files).

`apply` fetches each git source, moves its clone to the fetched commit,
shows one preview (every entry with its destination, then a diff wherever
existing content changes), asks, then places the files. With no selector
each entry follows its policy: `sync` keeps up with the source, `once`
places a file once and leaves it, and `manual` waits for `apply PKG`.
Naming packages or items, or `--all`, places them whatever their policy.
A `link` is a link only under `sync`, else a copy. A local file in the way
is never destroyed: it moves to `dotfiles.kept_dir`, and the row says
where. A file edited on this machine waits for a person, and `apply PKG`
refuses it unless `--force`.

An input a target uses (`{{ git_profile }}`) is asked at a terminal and
kept in `config.yml`; `--var NAME=VALUE` and `--vars-file` answer it
ahead, on `apply` or `add`, for every package named (or, with no
selector, every package that is on) that declares the name:

```bash
untaped dotfiles add git --var git_email=alex@example.com
untaped dotfiles apply --var git_profile=work
```

A selector is `PKG`, `PKG/ITEM`, `SOURCE/PKG` or `SOURCE/PKG/ITEM`; a
bare name that two sources offer exits 2, naming both spellings.

Edit linked files in your own checkout, subscribed as a folder: an edit
through a link lands in the managed clone, which `apply` then leaves where
it is until the change is discarded.

## A new machine

The machine's choices all live in `config.yml`. Copy it to a new machine
(or set a source's `dotfiles.sources.<name>.url` with `untaped config
set`), and `untaped dotfiles apply` clones each git source it names and
places what is on.

## Keep up to date

```bash
untaped dotfiles status
untaped dotfiles status --check  # exit 3 while there is something for apply or a person
untaped dotfiles apply --yes     # each entry by its policy
untaped dotfiles apply fish      # this one whatever its policy
```

`status` reads nothing from the network: `behind` is as of the last fetch,
which `apply` (or `apply --dry-run`) makes. What each state means, what
`apply` does with it and what needs a person is the policy guide, the
docstring of
[`Policy`](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-dotfiles/src/untaped_dotfiles/policy.py).

`apply` exits 0 when done with nothing left for a person, 3 when done
with something left (an entry its policy only reports, or one waiting on
an input), 1 when an entry failed, and 2 without a terminal and without
`--yes`. A timer treats 3 as routine news, not a failure; the
`attention` file below counts what waits, and `untaped dotfiles status`
lists it:

```ini
# ~/.config/systemd/user/untaped-dotfiles.service
[Service]
Type=oneshot
ExecStart=%h/.local/bin/untaped dotfiles apply --yes
SuccessExitStatus=3
```

launchd has no such setting, so its `ProgramArguments` run
`sh -c 'untaped dotfiles apply --yes; c=$?; [ $c -eq 0 ] || [ $c -eq 3 ]'`.

## Show it in the prompt

Run without selectors, `status` writes the
[status files](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#dotfiles),
so a prompt segment needs only the shell:

```toml
# starship.toml
[custom.dotfiles]
command = "cat ~/.untaped/plugins/dotfiles/prompt/attention"
when = 'test "$(cat ~/.untaped/plugins/dotfiles/prompt/attention 2>/dev/null)" != 0'
format = "[⇣ $output dotfiles]($style) "
```

## From 11.x

A repo's `dotfiles.yml` becomes one package file per item under
`.untaped/dotfiles/`: each item is a package with one item, `source:`
becomes `path:`, and `skip:` on the machine becomes an item turned off
(`untaped dotfiles remove PKG/ITEM --keep-files`). Subscriptions and
enabled items are not carried over: 12.0 starts from a clean home, so
subscribe and add again.

## Reference

For an agent, `untaped docs dotfiles` is the reference, generated by the untaped you run: each command with its options and the settings ([untaped docs](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/docs.md)). Run `untaped skills install dotfiles --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)) so the agent knows to read it; for people, `untaped dotfiles COMMAND --help` covers one command.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#dotfiles) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#dotfiles)
