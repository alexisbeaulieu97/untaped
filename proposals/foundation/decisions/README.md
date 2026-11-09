# Decisions

One file per decision: the problem, what was decided, what was rejected and why, and what it rests on. A decision is never rewritten. When a later one changes it, the old record gets a "replaced by" or "amended by" line and the new record explains why the old view was wrong.

"Rests on" is the tower: a decision holds only while the ones it rests on hold. When a decision is reopened, everything that rests on it is checked again.

Statuses: **accepted**, **amended** (still holds, partly changed by a later record), **superseded** (no longer holds), **reopened** (under question).

Numbers follow the order in which decisions were settled. Names in code are placeholders unless a record says otherwise.

| # | Decision | Status | Rests on |
|---|---|---|---|
| [0001](0001-make-your-own.md) | Goals; "make your own" is the test | accepted | — |
| [0002](0002-words-engines-users.md) | Words, engines, users | accepted | 0001 |
| [0003](0003-extend-and-replace.md) | Two ways to plug in: extend and replace | accepted | 0001 |
| [0004](0004-words-without-a-center.md) | Shared words without a center | accepted | 0001, 0002 |
| [0005](0005-core-option-c.md) | Core keeps the shared words (option C) | superseded by 0020 | 0002, 0004 |
| [0006](0006-everything-else-is-a-plugin.md) | Everything else is a plugin | amended by 0020, 0026 | 0002, 0005 |
| [0007](0007-meaning-never-presentation.md) | Words describe meaning, never presentation | amended by 0036 | 0002 |
| [0008](0008-the-run-rule.md) | The run rule | amended by 0038, 0040 | 0001 |
| [0009](0009-unattended.md) | Everything runs unattended | accepted | 0001 |
| [0010](0010-one-process.md) | One process; boundaries only between tiers | accepted | 0001 |
| [0011](0011-placement-by-intent.md) | Placement check, by intent | amended by 0020 | 0001 |
| [0012](0012-two-engines-refuse.md) | Two engines for one slot: refuse | amended by 0029, 0032, 0039 | 0003 |
| [0013](0013-versioning-per-package.md) | Versioning: the package, floors only | amended by 0035, 0048 | 0002 |
| [0014](0014-bundles-words-never-name-engines.md) | Bundles; words never name an engine | amended by 0034 | 0002, 0003, 0013 |
| [0015](0015-builder-owns-the-list.md) | The builder's record is the only plugin list | accepted | 0010 |
| [0016](0016-engines-depend-on-words.md) | An engine needing an engine depends on words | accepted (details parked) | 0002 |
| [0017](0017-stay-in-python.md) | Stay in Python | accepted | 0010, 0015 |
| [0018](0018-zero-dependency-core.md) | Core has no third-party dependencies | amended by 0020 | 0005 |
| [0019](0019-schema-words.md) | Schema words and a converter slot | superseded by 0020 | 0005, 0018 |
| [0020](0020-core-is-the-kernel-only.md) | Core is the plugin kernel only (option B) | accepted | 0004, 0018 |
| [0021](0021-package-line-rule.md) | Package lines and identities | accepted | 0002, 0013, 0020 |
| [0022](0022-ranked-buckets.md) | Ranked buckets | amended by 0046, 0047 | 0020, 0021 |
| [0023](0023-words-bucket.md) | The words bucket | accepted | 0022 |
| [0024](0024-grammar-bucket.md) | The grammar bucket (A, R, G1–G6) | amended by 0035, 0049 | 0022, 0023 |
| [0025](0025-parties-bucket.md) | The parties bucket (E, X, V, W) | accepted | 0022, 0024 |
| [0026](0026-kernel-stripped.md) | The kernel stripped to the plugin foundation | amended by 0049 | 0020, 0022 |
| [0027](0027-kernel-extension-points.md) | The kernel's own extension points are ordinary ones | amended by 0049 | 0026 |
| [0028](0028-wrappers-leave-the-kernel.md) | Wrappers leave the kernel; a `Dispatch` slot stays | amended by 0050 | 0027 |
| [0029](0029-resolvers.md) | Ambiguity fails by default; resolvers | amended by 0049 | 0012, 0027 |
| [0030](0030-loop-and-bootstrap.md) | The loop rule and the bootstrap closure | accepted | 0029 |
| [0031](0031-call-grammar.md) | A call grammar package | amended by 0051 | 0024, 0028 |
| [0032](0032-picks-and-order-are-resolvers.md) | Picks and provider order belong to resolvers | accepted | 0029 |
| [0033](0033-mechanics-convention.md) | The mechanics convention | accepted | 0029, 0032 |
| [0034](0034-bundles-definition.md) | Bundles: a definition plus conventions | accepted | 0014, 0029 |
| [0035](0035-pydantic-is-the-notation.md) | Pydantic is the notation of untaped's language | accepted | 0020, 0024 |
| [0036](0036-engine-hints.md) | An engine's hints are its own words | accepted | 0007, 0035 |
| [0037](0037-concurrency.md) | Concurrency: `Capacity` and "stopped" | accepted | 0008, 0024 |
| [0038](0038-settings-and-no-run-family.md) | Settings through a slot; a run is a scope | amended by 0049 | 0008, 0026, 0037 |
| [0039](0039-undecided.md) | `Undecided` | accepted | 0029, 0030 |
| [0040](0040-scope-bindings-noscope.md) | Scope bindings, `NoScope`, threads | accepted | 0038 |
| [0041](0041-process-slot-and-host.md) | Process facts and the host role | accepted | 0040 |
| [0042](0042-role-vs-bucket.md) | Role vs bucket; what a plugin declares | amended by 0048 | 0022, 0026 |
| [0043](0043-contract-split.md) | `Contract` splits: mechanism vs language | accepted | 0024, 0026 |
| [0044](0044-extend-answers-merge.md) | Extend answers; askers merge, never name | amended by 0051 | 0003, 0032, 0043 |
| [0045](0045-two-kinds-of-order.md) | Two kinds of order | reopened 2026-11-06 | 0029, 0032 |
| [0046](0046-rank-3-parties.md) | Rank 3 is named "parties" | accepted | 0022 |
| [0047](0047-architecture-vs-design.md) | Architecture vs design | accepted | 0022, 0026 |
| [0048](0048-kernel-requirement-rule.md) | The kernel requires only what it needs | accepted | 0042, 0047 |
| [0049](0049-kernel-hooks-loaded.md) | Kernel hooks; `Loaded` replaces `OwnerCheck` | accepted | 0027, 0047 |
| [0050](0050-dispatch-question-hook.md) | `Dispatch` is a question hook | accepted | 0028, 0049 |
| [0051](0051-reply-and-result.md) | Extend results: `Reply` and `Result` | accepted | 0044, 0050 |
| [0052](0052-call-shape.md) | The `Call` shape (F3) and `PluginId` | amended by 0056 | 0050, 0051 |
| [0053](0053-founding-principle.md) | The kernel's founding principle | accepted | 0047, 0048, 0049, 0050, 0051, 0052 |
| [0054](0054-recording-is-atomic.md) | Recording is atomic | accepted | 0049, 0053 |
| [0055](0055-document-shape.md) | The shape of this document | accepted | 0047 |
| [0056](0056-multi-host-parked.md) | Multi-host is parked; rules wait in Pending | accepted | 0052, 0055 |
