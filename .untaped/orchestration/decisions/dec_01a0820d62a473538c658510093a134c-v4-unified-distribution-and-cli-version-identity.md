+++
schema = "untaped.orchestration.decision/v1"
id = "dec_01a0820d62a473538c658510093a134c"
kind = "decision"
title = "v4 unified distribution and CLI version identity"
created_at = "2026-09-08T17:25:42.029Z"
tags = []

[[links]]
relation = "supersedes"
target_store_id = "sto_019f68b6af9e721e970126ca31dbfde1"
target = "dec_019f68b6b8fe72f49640813dd3240a0c"
+++
Version 4 has one distribution and one executable identity. `untaped --version`
reads the installed `untaped` distribution metadata and prints that exact
version followed by a newline. The release unit is the unified package, whose
wheel and source archive include the composed application and built-in
capabilities.

Release smoke checks bind package metadata, the executable, and root and
capability help together. Standalone tool distribution overrides and retired
standalone executable assumptions are outside the v4 contract.
