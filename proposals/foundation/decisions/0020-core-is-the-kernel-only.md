# 0020. Core is the plugin kernel only (option B)

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0004](0004-words-without-a-center.md) words without a center · [0018](0018-zero-dependency-core.md) core has no third-party dependencies
- **Replaces:** [0005](0005-core-option-c.md) core keeps the shared words (option C) · [0019](0019-schema-words.md) schema words and a converter slot (most of it dissolved)
- **Amends:** [0006](0006-everything-else-is-a-plugin.md), [0011](0011-placement-by-intent.md) ("core reads it" now means the kernel), [0018](0018-zero-dependency-core.md) (now true by construction)
- **Reopen if:**
  1. one data library (pydantic) stops being acceptable as the implementation of the command words for every command plugin, or someone needs to swap the converter apart from the vocabulary;
  2. the kernel needs to read the families (a command, a setting) itself;
  3. competing command vocabularies become common enough that bundles can't keep one standard set;
  4. start-up becomes unacceptable with pydantic loaded by the command words (measured: pydantic import plus one model ≈ 108 ms; `pydantic_settings` ≈ 195 ms).

## Problem

Under option C, core owned the words every plugin speaks (Command, Record, Settings, errors, the run). Those words are data-shaped: at untaped's boundaries they must be converted and validated (CLI arguments, MCP JSON, YAML and env settings, rendered and piped records). Doing that well takes a library like pydantic. So core either depended on pydantic (an engine in core) or rebuilt it (0019's growing pile: `load`, `Json`, `Clean`, `@check`, `UNSET`, a ten-rule conformance kit).

## Decision

- **`untaped-core` is the kernel only:** plugins, contracts, the replace slot; loading and failures; standard library only. It also gets a per-run scope, so slots resolve per run under the run rule.
- **The families move to a words package** (placeholder `untaped-command`) that **depends on pydantic as a library**. `Command`, `Settings` and `Record` are pydantic models; authors write normal pydantic; edge engines use pydantic to convert, validate and produce JSON Schema.
- "Words depend only on core and other words" becomes **"words never depend on an engine; libraries are allowed and visible"**. The old rule existed to stop words pulling in engines.
- **Swapping moves up a level.** Nobody swaps the converter. Someone who wants a different data model publishes competing command words (0004).
- untaped handles only data that crosses its boundaries: command inputs, settings, records, errors and messages. A plugin's own data (API payloads, files, state) is its own business, with any library.

```python
# ✓ a plugin under B (names are placeholders)
from untaped_command import Command, Settings, Record, Field

class JiraSettings(Settings):            # a pydantic model
    page_size: int = Field(default=50, gt=0)

class Sprint(Record):
    id: int

class List(Command):
    board_id: int | None = None
    def run(self) -> Iterator[Sprint]: ...
```

## Rejected

- **C** (core owns the words): it assumed core's few words could stay pure meaning. Data-shaped words can't: they bring a validation library, or a rebuilt one, with them.
- **A** (core holds what every plugin needs): would win only if engines never needed to be swapped.

## Consequences

- Re-run of earlier decisions: unchanged (words/engines/users, extend/replace, meaning not presentation, unattended, processes, placement, bundles, environments, engines needing engines, Python); stronger (core owns no vocabulary; a pydantic major is a words major, not a kernel major; the kernel has no dependencies by construction; the kernel is already its own package for the runner/hub goal).
- Costs accepted: nearly every command plugin depends on pydantic through the command words; the kernel can't check commands at load by itself (it needs "an owner checks what plugins declare with its words", see [0049](0049-kernel-hooks-loaded.md)); the startup declaration cache becomes general (its owner is open); competing command words could split plugins into groups that can't share a front end (0004 accepts this; bundles ship the standard words); a bigger migration.
- From 0019, gone: the schema slot, the conformance kit, `load`/`Json`/`Clean`/`@check`/`UNSET`, the keyword-only `Field` workaround.

## How we got here

The maintainer restarted 0019 and asked *why* untaped needs to describe, convert, check and clean data at all. The answer, "only where a generic engine handles data on a plugin's behalf", showed that most of 0019's machinery came from mixing boundary data with a plugin's own data. What remained was one real tension, and it broke C's founding assumption. Lesson kept: a growing pile of special cases means a premise is false; trace it back.

What would have made C win: the families being pure definitions with no library behind them, or the kernel genuinely needing to read commands or settings.
