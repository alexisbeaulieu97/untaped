+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6b6f0748b8a01f0cbe11b7f12"
kind = "decision"
title = "The SDK ships no core agent skill"
created_at = "2026-07-10T00:30:02.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "git:80bb8411cd0017f3e0cde818656aaf6fd0233368:docs/decisions.md#sha256:597d74559b5447942468b7fe321ab40dccbed32e4055d9fca71830702c55831e"
+++

The SDK does **not** package a core agent skill. The old
`skill_assets/untaped/SKILL.md` taught the retired plugin CLI and is obsolete.
Per-tool `SKILL.md`s (installed via each tool's `skills` group) cover tool
usage, and guidance on building tools with the SDK lives in the README and
`docs/`, not in a packaged agent skill. (The file is removed later, in Phase 3;
this entry only records the decision.)
