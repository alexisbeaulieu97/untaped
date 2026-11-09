# 0012. Two engines for one slot: refuse

- **Status:** amended · settled before 2026-11-05 (7.1) · the maintainer
- **Rests on:** [0003](0003-extend-and-replace.md), extend and replace
- **Amended by [0029](0029-resolvers.md):** the kernel has no picks. When several engines fill a slot, it asks resolvers, and `untaped-plugins` fills `Resolver` from what the user declared.
- **Amended by [0032](0032-picks-and-order-are-resolvers.md):** "the pick is an ordinary setting in the owner's section" no longer holds. Per-slot picks belong to resolvers, not to the slot owner's settings.
- **Amended by [0039](0039-undecided.md):** several engines with no resolver answer is `Undecided` ("nothing chose: declare a preference"), not `Ambiguous` (which now means the resolvers disagree).
- **Amended by [0026](0026-kernel-stripped.md) / [0030](0030-loop-and-bootstrap.md):** "a slot needed before plugins load gets its pick from the environment or core's own small file" became: the host's own resolver reads it. The kernel keeps no files.
- **Reopen if:** real one-of slots turn out to be common, so picks become a chore. The rejected options (owner-named defaults or priorities) would then win.

## Problem

Two engines are installed for one replace slot, and nothing says which to use.

## Decision (as it was)

- Refuse, with the fix, **only in the run that uses that slot**. Everything else keeps working. Example message: "`git.repo_store` has two engines: git-cli, acme-git-dulwich. Pick one: `untaped plugin use git.repo_store <name>` or `UNTAPED_GIT__…`".
- The pick was an ordinary setting in the owner's section, next to `extensions.<contract>.rank`, following profiles and projects, and settable from the environment. (Amended: see above.)

## Rejected

- **First or last installed wins.** The composition rule says results never depend on install order.
- **The words' owner names a default engine.** It breaks "the owner never names who answers".
- **Engines declaring priorities.** Two "preferred" engines are the same problem again.

## Consequences

True one-of slots are rarer than they sound. Front ends coexist, and renderers and config sources extend. Real one-of slots are things like the repo store and where new secrets are written.
