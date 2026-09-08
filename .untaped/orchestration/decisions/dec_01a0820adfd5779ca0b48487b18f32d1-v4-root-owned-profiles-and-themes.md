+++
schema = "untaped.orchestration.decision/v1"
id = "dec_01a0820adfd5779ca0b48487b18f32d1"
kind = "decision"
title = "v4 root-owned profiles and themes"
created_at = "2026-09-08T17:24:08.571Z"
tags = []

[[links]]
relation = "supersedes"
target_store_id = "sto_019f68b6af9e721e970126ca31dbfde1"
target = "dec_019f68b6b3da73acb58a68dffdc85adc"
+++
Version 4 keeps profiles, themes, and shared settings under the unified root.
`untaped profile ...` manages the shared profile layout, and `untaped config
...` reads and writes fully qualified `section.key` settings. The root
`--profile` option selects an invocation profile; the default profile layers
beneath the selected profile.

Capabilities own their profile and managed-state fields. State fields are
disjoint from profile fields and are not writable through `untaped config`.
Themes are root UI settings. Standalone profile or theme command packages are
outside the v4 contract.
