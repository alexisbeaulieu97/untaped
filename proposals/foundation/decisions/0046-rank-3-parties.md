# 0046. Rank 3 is named "parties"

- **Status:** accepted · 2026-11-06 · the maintainer
- **Rests on:** [0022](0022-ranked-buckets.md) ranked buckets

## Problem

Rank 3 holds code that does things: engines, users, hosts. Its draft name, "engines and users", named two roles, and [0042](0042-role-vs-bucket.md) separates roles from buckets. The maintainer liked the language scheme (kernel · words · grammar · ___ · bundles) and wanted a name that fits it.

Criteria: one metaphor with words and grammar; covers every role (engine, user, host) without favouring one; no collision with existing terms (engine, action, worker).

## Decision

**Parties.** A contract's parties each hold a role (offerer, filler, user), and "third party" becomes literal: a party that isn't the owner.

Ranks: **kernel · words · grammar · parties · bundles.**

## Rejected

| Name | Why |
|---|---|
| doing | a working name only |
| speakers, works, acts, voices, texts | the maintainer didn't like them |
| semantics | would put meaning in two places (words are meaning) |
| pragmatics | fits (Morris 1938: semantics, syntax, pragmatics), but unfamiliar |
| usage, discourse, practice | offered, not chosen |
| speeches (chosen 2026-11-05) | names what the packages produce, not what they are |
| actors (the maintainer's proposal) | collides with the audit actor (the person who acted) and the actor model; the person is always "the user" |
| performers, players, doers | considered; performers was the fallback |

## How we got here

doing → speeches (2026-11-05 evening) → parties (2026-11-06).
