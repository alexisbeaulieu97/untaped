# failures carry category and system; exit codes 4/5; JSON diagnostics

Decision ID: `dec_01a0eb46316e730099c937b014f81525`

An agent working a ticket must know from a failure alone whether to fix its
change, fix the environment, or retry. Prose on stderr and a blanket exit `1`
could not tell it, so it would start "fixing" code while its token was expired.

- Every `UntapedError`, and every failed row, carries a `category` (`usage`,
  `config`, `auth`, `permission`, `not_found`, `invalid`, `conflict`,
  `unavailable`, `failed`, `interrupted`) and a `system` (`untaped`, `local`,
  `git`, a service section, or a capability's refinement such as `awx.scm`).
  Classes declare defaults; instances override. The category alone selects the
  exit code and retryability, so the two can never disagree.
- Exit `4` means the environment needs fixing (config, auth, permission) and
  `5` means temporary (`unavailable`); `0`, `1`, `2`, `3` and `130` keep their
  meaning. One run exits with the most severe failure it saw:
  `130 > 2 > 4 > 5 > 1 > 3 > 0`. Failures are counted in a per-invocation
  ledger by the shared reporting helpers, so capability code never threads
  categories to `finish()` by hand.
- Attribution is set at the source: the HTTP client knows the status and its
  section; capability mappers keep and refine it; code that replaces or
  swallows an error keeps the exception (`attribution()`, `ErrorInfo`), never
  a flattened string.
- `ConfigError` means local setup. An invalid input file or value is
  `invalid` (exit 1), even when it historically raised `ConfigError`.
- Diagnostics stay off stdout. With `--format json|yaml|pipe` or
  `UNTAPED_DIAGNOSTICS=json`, stderr is JSON Lines; the pipe envelope and data
  streams are unchanged, so `accept_kinds` consumers keep working.
- Failed outcome and target rows gain an optional `error` object; `detail`
  stays for humans. `error` is a reserved record field.

This breaks scripts that test `== 1` and the SDK's error shape, so it shipped
in untaped 9.0 with capability API 3.0.
