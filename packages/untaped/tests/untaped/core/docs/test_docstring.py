"""What a docstring's page says: fenced blocks pass through as written."""

from __future__ import annotations

from untaped.core.docs._docstring import describe, markdown

GUIDE = """How a rule is written.

Read first.

```python
@rule("no-print", reads=Tree)
def no_print(tree: Tree) -> None: ...
```

After the ``fence``, the guide goes on.
"""


def test_a_fenced_block_is_code_even_with_a_decorator_line() -> None:
    body = describe(GUIDE).body
    assert '```python\n@rule("no-print", reads=Tree)\n' in body
    assert body.endswith("After the `fence`, the guide goes on.")


def test_rst_double_backticks_become_markdown_ones_but_a_fence_stays() -> None:
    assert markdown("``a`` and ```yaml") == "`a` and ```yaml"
