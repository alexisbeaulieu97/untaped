# 0010. One process; boundaries only between tiers

- **Status:** accepted · settled before 2026-11-05 (foundation point 9; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0001](0001-make-your-own.md), the goals (runners, hubs and front ends as tiers)
- **Reopen if:** one environment per set of plugins stops being acceptable, because library conflicts become common or non-Python plugins become a goal. The rejected option (out-of-process plugins) would then win.

## Problem

The original pasted document left out-of-process plugins and isolation as an open question. Separate processes per plugin would isolate them, but plugins share typed words ([0002](0002-words-engines-users.md)), and classes can't be shared across processes without serialization.

## Decision

- Plugins run **in one process** and share typed classes.
- Process boundaries exist **only between tiers** (runners, hubs, remote front ends), never between plugins.
- Tiers may even use other languages.

## Consequences

- This answers the original document's isolation question.
- Security stance (AGENTS.md): a plugin runs with the user's rights. We add no guards that only look like protection.
- One set of plugins means one Python environment ([0015](0015-builder-owns-the-list.md)), which shaped the language comparison ([0017](0017-stay-in-python.md)).
