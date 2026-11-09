# 0003. Two ways to plug in: extend and replace

- **Status:** accepted · settled before 2026-11-05 (foundation point 2; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0001](0001-make-your-own.md), the "make your own" test
- **Reopen if:** no capability is really one-of. The rejected option (extend only) wins if every one-of case turns out to be expressible as "ask all, the asker picks".

## Problem

There are two different relationships between a capability and the code that provides it:
- GitHub and GitLab both **answer** "which repos exist?";
- ansible needs exactly **one** thing that can check out a repo.

Today only the first exists (contracts). Credential stores are another case of the second (#1018: 1Password, Keychain, pass).

## Decision

Two ways to plug in:
- **Extend contract:** the owner asks, every provider answers, and the asker decides what to do with the answers (today's contracts).
- **Replace slot:** exactly one engine fills it (new). Its code name is open (`Slot`? `Service`, dsh's term?).

Behaviour when an engine is missing: with git's words installed and no engine, ansible's other commands still work, and only the commands that need a repo stop. Git alone still clones a URL you hand it. Lookups by name and host logins go quiet until github or gitlab is installed.

## Consequences

- True one-of slots are rarer than they sound. Front ends coexist, and renderers and config sources extend. Real one-of slots are things like the repo store and where new secrets are written ([0012](0012-two-engines-refuse.md)).
- Two engines installed for one slot needed its own decision ([0012](0012-two-engines-refuse.md)), and later resolvers ([0029](0029-resolvers.md)).
