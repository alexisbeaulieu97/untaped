+++
schema = "untaped.orchestration.decision/v1"
id = "dec_01a0820d443870069dfe37af04c1fcc2"
kind = "decision"
title = "v4 capability-owned packaged skills"
created_at = "2026-09-08T17:25:22.597Z"
tags = []

[[links]]
relation = "supersedes"
target_store_id = "sto_019f68b6af9e721e970126ca31dbfde1"
target = "dec_019f68b6b6f0748b8a01f0cbe11b7f12"
+++
Version 4 packages agent skills with capabilities. A capability declares its
`SKILL.md` assets on `CapabilitySpec`; the unified root discovers the accepted
union and `untaped skills list` or `untaped skills install` manages it. Stable
asset IDs such as `untaped-github` remain available, while short selectors
such as `github` are convenient root-level inputs.

There is no separate core agent skill or standalone skill-management package.
Provider authoring guidance belongs in the public provider documentation, and
capability-specific usage belongs in the capability's packaged skill.
