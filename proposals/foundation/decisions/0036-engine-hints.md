# 0036. An engine's hints are its own words

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0007](0007-meaning-never-presentation.md) meaning, never presentation · [0035](0035-pydantic-is-the-notation.md) pydantic is the notation
- **Amends:** [0007](0007-meaning-never-presentation.md) (hints are no longer presentation inside untaped's words)

## Problem

Authors want to declare engine-specific presentation: `Short("-x")`, `Positional`, `Output(columns=…)`, `Column` marks on record fields. Under "words describe meaning, never presentation" these had no home. Putting them in untaped's words breaks that rule; putting them in the engine makes every command plugin depend on the CLI engine.

## Decision

- Each engine owner publishes **its own words package**: `untaped-cli-words` (`Short`, `Positional`), a table renderer's words (`Column`).
- Hints are attached through the notation's `Annotated` and **ignored by engines that don't know them**.
- A command plugin depends on those words, **never on the engine**.

```python
# ✗ presentation inside untaped's words
class List(Command):
    board_id: int = Field(cli_short="-b")

# ✓ meaning in the engine's own language, attached through the notation
from untaped_cli_words import Short
class List(Command):
    board_id: Annotated[int, Short("-b")]
```

## Consequences

- Invariant "words describe meaning, never presentation" holds: untaped's words carry no presentation, and a hint is meaning in the engine's own language.
- An MCP engine ignores `Short`; a hub's renderer ignores `Column` unless it speaks the table renderer's words.
