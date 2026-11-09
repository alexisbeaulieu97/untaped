# 0004. Shared words without a center

- **Status:** accepted · settled before 2026-11-05 (foundation point 3; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0001](0001-make-your-own.md) "make your own"; [0002](0002-words-engines-users.md) words, engines, users
- **Reopen if:** competing words plus bridges stops being workable and the ecosystem fragments badly. The rejected option (a central registry of words) wins if fragmentation becomes a real, recurring problem.

## Problem

If words are what everyone depends on ([0002](0002-words-engines-users.md)), who gets to publish them, and who settles disagreements about what they mean?

## Decision

- Anyone may publish words, and each set of words has **one owner**.
- Disagreement is handled by **competing words** plus **bridges** that translate between them.
- Core never decides between words.

Prior art:
- an API package separate from its implementation: OpenTelemetry api/sdk, SLF4J;
- namespaced, versioned kinds anyone can publish: Kubernetes CRDs, and Gateway API's GatewayClass, where one vocabulary has many controllers and the user picks one;
- structural typing: Go interfaces, Python `Protocol`, JSON Schema;
- adapters.

## Rejected

- **No owners at all, purely structural** ("anything with the right shape"). Nothing would name what a shape means.

## Consequences

- **Limit:** replacing a *vocabulary* breaks its consumers. No design avoids that; dsh has the same limit.
- Under option B ([0020](0020-core-is-the-kernel-only.md)) this became stronger: core owns no vocabulary at all, and a different data model means publishing competing command words.
