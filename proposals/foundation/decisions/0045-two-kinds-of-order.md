# 0045. Two kinds of order

- **Status:** reopened 2026-11-06 (settled 2026-11-05 · the maintainer)
- **Rests on:** [0029](0029-resolvers.md) resolvers; [0032](0032-picks-and-order-are-resolvers.md) picks and order belong to resolvers
- **Reopen if:** — (already reopened; see below)

## Problem

Review finding 4. Some fillers need an order: settings sources (an environment variable beats `config.yml`), call wrappers (cache outside retry). Under [0032](0032-picks-and-order-are-resolvers.md) only resolvers order, and without an agreed order the answer is `Ambiguous`. "env beats config.yml" answered with `Ambiguous` is absurd.

## Decision (as settled 2026-11-05)

Two kinds of order:

1. **Order built into the meaning, true for everyone:** the owner of an extension point defines roles and the rule between them, in its grammar (settings `Layer`: defaults < user < project < env < flag; wrapper `Position`: cache outer, retry inner).
2. **Taste between equals in the same role** (two vaults on one layer; github vs gitlab for one repo): resolvers, else `Ambiguous`.

Not a revival of [0012](0012-two-engines-refuse.md)'s rejection: that was engines claiming priority for themselves; here the owner defines roles once for everyone, and equals still tie.

## Why it was reopened (2026-11-06)

The maintainer doubted the mechanic. Two fresh reviewers (one through design principles, one through prior art) found real flaws:

- wrapper positions hand meaning to the user as taste (rate limit vs retry: the order changes behaviour, and a resolver would let a user pick wrong silently);
- the layer sat on the reader class, confusing the medium with the scope; a Vault or TOML reader serves several scopes. Put the scope on each supplied value (git config `--show-scope`, CSS origins);
- `ENV` and `FLAG` name CLI media inside grammar; name layers by intent;
- a closed `Enum` with numbers can't take an org layer without an owner release, and numbers invite "2.5";
- no enforcement axis: an admin value a flag must not override is a veto, not an order (CSS `!important`, VS Code and Chrome policies).

I then simulated options rank by rank, against tests that assumed undecided pieces ("can a third party add a role?" assumes roles exist and that third parties should add them). **The maintainer stopped it: build the tower one piece at a time.** A test may assume only what is settled.

## Where it stands

- **Proposed by the maintainer, under discussion:** all order is resolvers' work, and **resolvers declare what they order**. My error had been treating defaults < env < flag as a fixed problem; in a decentralized architecture nobody knows in advance which orderings will be needed, and the ordering mechanic already exists. One resolver may rank engines for slots, another settings sources.
- Under [0047](0047-architecture-vs-design.md), settings-source order and wrapper order are the settings and calls plugins' own design. **The only kernel question left:** does `Resolver` declare what it orders, and how do several resolvers for one thing combine (must agree, or combine non-contradicting partial orders, with a contradiction giving `Ambiguous` and a gap `Undecided`)?
- The `Layer` and `Position` text is under question until then.
