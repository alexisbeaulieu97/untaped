+++
schema = "untaped.orchestration.decision/v1"
id = "dec_01a0820ac45e77eab1dcf65e9eae55b0"
kind = "decision"
title = "v4 unified application and capability composition"
created_at = "2026-09-08T17:23:57.137Z"
tags = []

[[links]]
relation = "supersedes"
target_store_id = "sto_019f68b6af9e721e970126ca31dbfde1"
target = "dec_019f68b6b2cb75a9a7cb908963b4b59c"
+++
Version 4 ships one `untaped` application and executable. The root owns shared
configuration, profiles, skills, doctor checks, capability reporting, and root
options; built-in capabilities mount under `untaped <capability> ...`.

External providers are discovered through the `untaped.capabilities` entry
point group and validated against `untaped.capability_api` before they mount.
External provider failures are quarantined so the root can continue; built-in
violations are fatal. Standalone per-capability executables and the retired
standalone composition helpers are not part of the v4 application contract.
