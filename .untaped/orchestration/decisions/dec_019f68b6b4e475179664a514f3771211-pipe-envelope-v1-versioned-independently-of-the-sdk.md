+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6b4e475179664a514f3771211"
kind = "decision"
title = "Pipe envelope v1 — versioned independently of the SDK"
created_at = "2026-07-10T00:30:02.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "git:80bb8411cd0017f3e0cde818656aaf6fd0233368:docs/decisions.md#sha256:597d74559b5447942468b7fe321ab40dccbed32e4055d9fca71830702c55831e"
+++

The `--format pipe` envelope (NDJSON; see [`src/untaped/pipe.py`](../src/untaped/pipe.py))
is declared **v1** and is **frozen and stable across SDK 1.x *and* 2.x**. The
envelope carries its own version number (`"untaped": "1"`) and is versioned
independently of the SDK: it did not change when the SDK went to 2.0 (that was a
config-layout break, not a pipe break). Any change to the envelope shape would
bump the envelope version, regardless of the SDK version it ships in.

Tools pipe across separate `uv tool` environments that may run different SDK
versions, so the envelope is a cross-tool wire contract: this freeze is what
guarantees `untaped-github | untaped-ansible` works regardless of which SDK
version each tool was built against.

Record fields remain producer-owned, but filesystem mutation consumers need a
generic target contract that does not require understanding another tool's
domain. When a pipe record names a concrete filesystem target, producers put it
in `record.target_path` as an absolute, non-empty path. Producers omit
`target_path` when no concrete target exists; they do not emit `""` or `null` as
a target. Domain fields such as `path`, `workspace`, `repo`, or `full_name` may
remain for display and templating, but consumers should not branch on a
producer-specific `kind` just to locate the target. Pipe records whose `kind`
ends in `.summary` are informational summaries rather than filesystem targets,
and target consumers may skip them. This is a record-level convention and does
**not** change the v1 envelope shape.
