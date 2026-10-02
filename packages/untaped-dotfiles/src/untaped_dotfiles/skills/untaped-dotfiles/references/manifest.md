# The manifest

`dotfiles.yml` at the repo root (`subscribe --manifest PATH` names another
file). `version: 1` is the only version; a newer one is refused.

```yaml
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
      - source: fish/conf.d   # a directory: each file under it is placed
        target: ~/.config/fish/conf.d
  claude:
    policy: sync
    files:
      - name: settings        # names a file so a machine can skip it
        source: claude/settings.json
        target: ~/.claude/settings.json
        mode: merge
        unless: [claude-plugin-dev]
      - source: claude/skills
        target: ~/.agents/skills/mine
      - source: claude/skills # the same source may go to several targets
        target: ~/.claude/skills/mine
```

## Rules

- Item names: `[a-z0-9][a-z0-9._-]*`, unique by construction.
- `source` is relative to the repo root and stays inside it: no `..`, no
  absolute path.
- `target` starts with `~/` or `/`, no `..`. The home directory itself, or
  a parent of it, is refused. No variable expansion, no templating.
- `mode` defaults to `link`. `merge` needs a JSON or YAML target, chosen by
  the extension (`.json`, `.yml`, `.yaml`) or an explicit `format:`.
  `merge` does not apply to a directory source.
- `policy` on an item is a suggestion: `sync`, `once` or `manual`; `enable
  --policy` overrides it on a machine.
- `os` on an item or a file: `macos`, `linux`, `windows`. `only` and
  `unless` on an item or a file: tag lists. An entry applies when the
  machine has any `only` tag (or `only` is absent) and none of the `unless`
  tags. Tags are the `dotfiles.tags` setting; the OS is detected, or
  `dotfiles.os` overrides it.
- `name` on a file defaults to its `source` and is what `enable --skip`
  takes. The same target twice in one item is an error; the same source
  twice is fine, and skipping it by source skips both placements.

## Modes

| Mode | Places | Follows the repo |
|---|---|---|
| `link` | A symlink into the working tree of the clone or checkout. | Live, so the policy governs when a managed clone is pulled. Under `once` the file is placed as a copy. |
| `copy` | The file's content; the hash of what was written is recorded. | Only when the tool copies again. |
| `merge` | The source document's keys deep-merged into the target document. Mappings recurse; scalars and lists replace. Keys the source does not mention stay. | Per managed key, like `copy`. |

`copy` and `merge` files in a managed clone are read from the fetched ref
(`origin/<ref>`), so they follow their policy even while a `manual` link
file holds the working tree back. `link` files in a registered checkout
show whatever the checkout holds.

## Directory sources

A directory source is placed per child: the target is a real directory and
each file under the source gets its own link or copy at the same relative
path. Files other programs write into the target directory are never
touched. Each placed file has its own status row; a file that disappears
from the source becomes `orphan`.
