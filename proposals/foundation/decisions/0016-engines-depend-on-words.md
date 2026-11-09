# 0016. An engine needing an engine depends on words

- **Status:** accepted as a principle; details parked · settled before 2026-11-05 (7.5) · the maintainer
- **Rests on:** [0002](0002-words-engines-users.md) words, engines, users
- **Reopen if:** "where files go" and similar capabilities can't be words without a bootstrap deadlock. The rejected option (engines bundled together) would win if chains of slots become too fragile to explain.

## Problem

Can an engine require another engine? A cache engine needs somewhere to put files.

## Decision

Yes. "User" is a role, so an engine that needs something depends on **words**, never on another engine.

```python
# ✗ the cache engine imports the ~/.untaped engine
from untaped_home.engine import home_dir

# ✓ it asks for the "where files go" words; any engine fills them
root = ask(files_home).root()
```

"Where files go" is its own capability by the placement check ([0011](0011-placement-by-intent.md)): `~/.untaped`, XDG folders, a project-local `.untaped/`, a temp folder per run on a runner, per user on a hub. Cache, state, locks and credentials would ask it.

## Consequences

Parked details:
- a broken chain should be explained ("cache can't run: it needs `files.home`, which nothing fills"), and cycles refused at load. Cycles were later settled by the loop rule ([0030](0030-loop-and-bootstrap.md));
- bootstrap: core writes its declaration cache before plugins load, so it needs an env var or a core default. Later the kernel stopped keeping files ([0026](0026-kernel-stripped.md)), and the cache's owner is an open question;
- on a hub the answer differs per user, so it is per run;
- cleanup when a plugin is removed (an earlier idea: its folder waits for `untaped setup prune`).
