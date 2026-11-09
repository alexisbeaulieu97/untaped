# 0035. Pydantic is the notation of untaped's language

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0020](0020-core-is-the-kernel-only.md) core is the kernel only (its condition 1) · [0024](0024-grammar-bucket.md) the grammar bucket
- **Amends:** [0024](0024-grammar-bucket.md) (G3's wording) · [0013](0013-versioning-per-package.md) (an accepted partial lockstep)
- **Reopen if:** someone needs to swap the converter apart from the vocabulary (0020's condition 1).

## Problem

Three questions arrived together:
- the test against today's code found a missing **value category** (`GitUrl`, `UtcTimestamp`, `Duration` are what records *have*, but they are not Records, Settings or errors);
- the maintainer asked "why force JSON?": G3's "turned into JSON" came from today's code, while a wire format is an engine's choice;
- the maintainer: "the schema engine is what validates the language." Does pydantic need its own bucket?

## Decision

- **Pydantic is the notation of untaped's language.** A language = a notation + words + grammar. The notation is a **property of the language, not a bucket**: no plugin publishes a notation, and its validation is the language's validation. The kernel never depends on it. A rival vocabulary may choose another notation; that is a new language.
- **G3: only what the notation can convert crosses**, checked at load with a `TypeAdapter` per parameter and answer. Formats (JSON, YAML, the CLI's text, a hub's binary) are each engine's choice.
  - ✗ `def repos(self, client: httpx.Client)` → `TypeAdapter` raises `PydanticSchemaGenerationError`
  - ✓ `def repos(self, owner: str) -> list[Repo]`
- **No `Value` category.** Value words are notation-annotated types:
  - ✓ `type GitUrl = Annotated[str, StringConstraints(...)]`
- **A value's human spelling belongs to its word's owner**, via a validator: `"6h"` for `Duration` is `Annotated[timedelta, BeforeValidator(parse_duration)]` (plain `timedelta` refuses `"6h"`).
- **A notation major (pydantic 3) is a major of every words and grammar package written in it, at once:** a named, accepted partial lockstep under 0013 (one environment holds one pydantic).

## Rejected

- **A `Value` base class:** not needed once the notation defines plain data.
- **A bucket for the notation:** it would hold one third-party library and nothing else.

## Consequences (sweep)

- G5 needs no new mark: the notation has `SecretStr`/`Secret[T]`, and engines detect the type.
- G1's readable form is the notation's JSON Schema (MCP tool schemas).
- Record's "survives a JSON round trip" becomes "dumps and reloads to the same value through the notation".
- Engine-specific hints were reframed: the notation's `Annotated` lets one field carry words from several languages ([0036](0036-engine-hints.md)).
- Unaffected: the kernel, resolvers, `Dispatch`, the call grammar, bundles.

Facts verified with pydantic 2.13.3: `TypeAdapter(SomeClient)` raises; `timedelta` dumps as `"PT6H"` in JSON mode and as a `timedelta` in Python mode.
