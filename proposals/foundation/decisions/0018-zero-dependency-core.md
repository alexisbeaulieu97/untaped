# 0018. Core has no third-party dependencies

- **Status:** amended · settled before 2026-11-05 (7.7) · the maintainer
- **Rests on:** [0005](0005-core-option-c.md), core = option C (superseded)
- **Amended by [0020](0020-core-is-the-kernel-only.md):** under option B it holds for the kernel **by construction**. The families leave core, and pydantic goes to the words packages as their library, not to a schema engine.
- **Reopen if:** a capability the kernel truly needs has no standard-library equivalent (e.g. version parsing: whether the kernel keeps `packaging` is still open).

## Problem

Core's dependencies are everyone's dependencies ([0017](0017-stay-in-python.md)). Today core depends on cyclopts, docstring-parser, filelock, httpx, packaging, prompt-toolkit, pydantic (<3), pydantic-settings, pyyaml, rich, ruamel.yaml and truststore.

## Decision

Core has zero third-party dependencies. The principle under it: **core depends on no engine.** Pydantic mixes words (`BaseModel`, `Field`) with an engine (validation, conversion, JSON Schema, serialization), the same defect as git's `api`.

Where today's dependencies go:
- cyclopts, docstring-parser → the CLI engine;
- rich, prompt-toolkit → the display and prompts engines;
- pyyaml, ruamel.yaml, pydantic-settings → the config engine;
- httpx, truststore → the HTTP plugin;
- filelock → the storage plugin;
- pydantic → the default schema engine (as it was; see [0019](0019-schema-words.md) and [0020](0020-core-is-the-kernel-only.md)).

Core's declarations use stdlib base classes made with `typing.dataclass_transform` (PEP 681), so type checkers see the fields as they do with pydantic and attrs. Core reads annotations with `annotationlib` (3.14).

## Consequences

- Words packages depend only on core and other words. Later reworded under B: words never depend on an *engine*, while libraries are allowed and visible.
- Measured: a pydantic `BaseModel` import plus one model takes ~108 ms; a stdlib dataclass ~20 ms; importing `pydantic_settings` alone ~195 ms.
