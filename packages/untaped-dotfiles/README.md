# untaped-dotfiles

Install it as part of `untaped`: `uv tool install 'untaped[dotfiles]'` or `pip install 'untaped[dotfiles]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`dotfiles` is [experimental](https://github.com/alexisbeaulieu97/untaped/blob/main/README.md#experimental) and may change in
a minor release.

`untaped dotfiles` places config files from one or more git repos on a
machine, with a policy per item per machine. A repo carries a manifest,
`dotfiles.yml`, of named *items*; each item lists *files* with a `mode`
(`link`, `copy` or `merge`). The manifest suggests a policy; the machine
decides when it enables the item: `sync` (new versions apply as they
arrive), `once` (apply, then leave alone) or `manual` (report that a new
version exists; apply when asked). Items not enabled are ignored.

The [packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-dotfiles/src/untaped_dotfiles/skills/untaped-dotfiles/SKILL.md)
and its references hold the per-command detail; `--help` lists the options,
and [`--columns '?'`](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#output-records) lists a command's fields.

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
wrote, so it never prompts and never loses a local edit. A clone is
fast-forwarded only when every enabled `link` file reading from it has
policy `sync` (and the working tree is clean); one `manual` link file
holds the clone back and `sync` reports those files as `behind`.
`apply ITEM` pulls the clone when the item has a link file in it, which
moves every other link file on that clone too; the plan lists them.

`status` reads nothing from the network. Every `sync` and `status` run
writes `status.json` and a one-line `attention` file under
`dotfiles.state_dir` (`apply` and `remove` refresh them too), so a prompt
segment needs only the shell:

```toml
# starship.toml
[custom.dotfiles]
command = "cat ~/.untaped/dotfiles/attention"
when = 'test "$(cat ~/.untaped/dotfiles/attention 2>/dev/null)" != 0'
format = "[⇣ $output dotfiles]($style) "
```

A timer is the machine's business; `untaped dotfiles sync` is what it
runs (a launchd agent on macOS, a systemd user timer on Linux).

## The manifest

```yaml
# dotfiles.yml, at the repo root
version: 1
items:
  fish:
    description: fish shell config
    policy: manual            # suggestion; absent means manual
    os: [macos, linux]        # absent means every OS
    files:
      - source: fish/config.fish
        target: ~/.config/fish/config.fish
        mode: link
      - source: fish/conf.d
        target: ~/.config/fish/conf.d
  claude:
    policy: sync
    files:
      - name: settings
        source: claude/settings.json
        target: ~/.claude/settings.json
        mode: merge
        unless: [claude-plugin-dev]
      - source: claude/skills
        target: ~/.agents/skills/mine
      - source: claude/skills
        target: ~/.claude/skills/mine
```

- `link` places a symlink into the checkout, so edits there are live;
  `copy` places a copy and detects local edits; `merge` deep-merges the
  source document (JSON or YAML, by the target's extension or `format:`)
  into a target another program also writes, touching only the keys the
  source names.
- A directory source is placed per child: the target directory stays a
  real directory, each file gets its own link or copy, and files other
  programs write there (a fish plugin manager's own `conf.d` files) are
  never touched or removed.
- `os`, `only` and `unless` (tags from the `dotfiles.tags` setting) filter
  items and files per machine; `enable ITEM --skip FILE` leaves one file
  out on this machine. The same source in two destinations is two entries.
- Under `once`, a `link` file is placed as a copy: a snapshot is what
  `once` means, and a symlink cannot be one.

On Windows, `link` needs symlink permission (Developer Mode or elevation);
`copy` and `merge` work. Nothing is tested there yet.

## Settings

`dotfiles.repos_dir`, `kept_dir`, `state_dir`, `tags` and `os` are in the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#dotfiles).
The machine's choices (which items, which policy, which files skipped)
and what was placed live in the state file, never in a profile.

## Output

Every command prints rows you can reshape with `--format` and `--columns`;
see [Scripting](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#dotfiles) and
[Exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#exit-codes).

## See also

- [Configuration](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/configuration.md) and the
  [configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#dotfiles).
