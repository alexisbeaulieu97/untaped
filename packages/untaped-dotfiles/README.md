# untaped-dotfiles

Install it as part of `untaped`: `uv tool install 'untaped[dotfiles]'` or `pip install 'untaped[dotfiles]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped dotfiles` places config files from one or more git repos on a
machine, with a policy per item per machine. A repo carries a manifest,
`dotfiles.yml`, of named *items*; each item lists *files* with a `mode`
(`link`, `copy` or `merge`). The manifest suggests a policy; the machine
decides when it enables the item: `sync` (new versions apply as they
arrive), `once` (apply, then leave alone) or `manual` (report that a new
version exists; apply when asked). Items not enabled are ignored.

`dotfiles` is [experimental](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/versioning.md#experimental) and may change in
a minor release.

## Set up

A repo needs a `dotfiles.yml` at its root:

```yaml
version: 1
items:
  fish:
    policy: manual            # suggestion; absent means manual
    files:
      - source: fish/config.fish
        target: ~/.config/fish/config.fish
        mode: link            # or copy, or merge (JSON/YAML)
      - source: fish/conf.d   # a directory: each file under it is placed
        target: ~/.config/fish/conf.d
```

Every entry key, the three modes, per-OS and per-tag filters and how a
directory source is placed are in [the manifest](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-dotfiles/src/untaped_dotfiles/skills/untaped-dotfiles/references/manifest.md).
Where clones, kept files and state live, and the machine's tags and OS,
are in the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#dotfiles).
On Windows, `link` needs symlink permission (Developer Mode or elevation);
`copy` and `merge` work. Nothing is tested there yet.

## Subscribe, enable, apply

```bash
untaped dotfiles subscribe git@github.com:acme/dotfiles.git
untaped dotfiles enable fish starship --policy sync
untaped dotfiles enable claude --skip settings
untaped dotfiles apply --dry-run
untaped dotfiles apply
```

`subscribe` clones a URL under `dotfiles.repos_dir`, or registers the
checkout at a path (fetched, never pulled: its owner pulls it). Several
repos can be subscribed: a personal one everywhere, a work one on the work
machine. `enable` records the machine's choice and places nothing;
`apply` shows the plan, asks, then places the paths. A local file in the
way is never destroyed: it moves to `dotfiles.kept_dir`, and the row says
where.

## Keep up to date

```bash
untaped dotfiles sync            # what a timer runs
untaped dotfiles status
untaped dotfiles status --check  # exit 3 while anything needs you
untaped dotfiles diff
untaped dotfiles apply fish
```

`sync` fetches every repo, applies the `sync` items and reports the rest.
It only ever writes a path that is absent or still exactly what the tool
wrote, so it never prompts and never loses a local edit. What each policy
does to a path in each state, and when a clone is pulled, is in
[policies](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-dotfiles/src/untaped_dotfiles/skills/untaped-dotfiles/references/policies.md).
A timer is the machine's business (a launchd agent on macOS, a systemd
user timer on Linux); `untaped dotfiles sync` is what it runs.

## Show it in the prompt

`status` reads nothing from the network. It writes the
[status files](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#dotfiles),
so a prompt segment needs only the shell:

```toml
# starship.toml
[custom.dotfiles]
command = "cat ~/.untaped/dotfiles/attention"
when = 'test "$(cat ~/.untaped/dotfiles/attention 2>/dev/null)" != 0'
format = "[⇣ $output dotfiles]($style) "
```

## Reference

The [packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-dotfiles/src/untaped_dotfiles/skills/untaped-dotfiles/SKILL.md) is the full reference: every command, the manifest, the policy table and the pitfalls. Install it for your agent with `untaped skills install dotfiles --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)); `untaped dotfiles COMMAND --help` lists each command's options.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#dotfiles) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#dotfiles)
