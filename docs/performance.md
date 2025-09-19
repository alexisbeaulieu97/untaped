# Performance Benchmarks

`tests/performance/` contains smoke tests that verify key operations stay within target thresholds. These tests run in-process (no external Tower calls) and ensure we notice regressions early.

## Validation

- **File:** `tests/performance/test_validation_performance.py`
- **Goal:** Average validation time < 100ms for minimal configurations.
- **Approach:** Validates a simple job template ten times and measures average time.

## Template Rendering

- **File:** `tests/performance/test_template_performance.py`
- **Goal:** Single template render < 50ms.
- **Approach:** Renders a small Jinja2 template multiple times and measures average run time.

## Running the Benchmarks

```bash
uv run pytest tests/performance -q
```

The thresholds are intentionally generous—they act as guardrails rather than micro-benchmarks. Adjust as needed if future enhancements change baseline performance.
