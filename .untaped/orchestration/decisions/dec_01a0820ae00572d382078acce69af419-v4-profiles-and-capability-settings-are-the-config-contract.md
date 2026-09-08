+++
schema = "untaped.orchestration.decision/v1"
id = "dec_01a0820ae00572d382078acce69af419"
kind = "decision"
title = "v4 profiles and capability settings are the config contract"
created_at = "2026-09-08T17:24:29.405Z"
tags = []

[[links]]
relation = "supersedes"
target_store_id = "sto_019f68b6af9e721e970126ca31dbfde1"
target = "dec_019f68b6b5ea758190f84ea79ed1cfbf"
+++
Version 4 uses `~/.untaped/config.yml` with `active`, `profiles`, and
capability-managed top-level state. `http` and `ui` are ordinary per-profile
settings. `untaped config` addresses settings by fully qualified names such as
`github.token` and `http.verify_ssl`; a capability owns its section.

The default profile layers beneath the active profile, and environment
overrides use `UNTAPED_<SECTION>__<FIELD>`. Capability state models are
disjoint from profile models and remain managed by their capability. Legacy
flat-layout compatibility warnings and standalone-tool key expansion are not
part of the current v4 contract.
