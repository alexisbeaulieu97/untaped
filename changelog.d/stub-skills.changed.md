`skills.updates` now defaults to `auto` and acts only on the home folder
(`~/.agents/skills`, `~/.claude/skills`): each run refreshes stubs an older
untaped wrote, leaves ones a newer untaped wrote or you edited, and converts
copies from 11.0 to stubs once, keeping the old copies under
`~/.untaped/backups/skills` (never removed automatically, safe to delete).
The first command after upgrading rewrites those folders. A project's own
skills are never written by a run; copies from an older untaped there are
warned about until `untaped skills update` runs in that project, which
rewrites a committed `.claude/skills` or `.agents/skills` folder (the diff
shows copies becoming stubs; `--dry-run` previews it). A setting
of `warn` or `off` is kept. This changes a default in a minor release.
