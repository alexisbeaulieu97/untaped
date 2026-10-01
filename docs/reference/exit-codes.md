# Exit codes

Every `untaped` command uses the same exit codes. Scripts can rely on them.

| Code | Meaning |
|---|---|
| 0 | Success. Per-item `skipped` rows are success too. |
| 1 | The thing you ran failed: a runtime failure, at least one failed item, a name that does not exist, input the remote or a local file rejected, or a declined confirmation (`cancelled; no changes made`). |
| 2 | Usage error, found before any side effect: an unknown flag, conflicting flags, a value out of range, no selection, or a prompt with no terminal and no `--yes`. |
| 3 | Predicate hit: the command worked, and the condition you asked it to check was true. |
| 4 | The environment needs fixing: missing or invalid settings or config file, a rejected token (HTTP 401), missing permission (HTTP 403), a missing tool such as `git` or `$EDITOR`. Retrying won't help, and neither will changing your code. |
| 5 | Temporary: the network is down, a request timed out, the service answered 5xx or 429, or another `untaped` process holds a lock. Retry later. |
| 130 | Interrupted with Ctrl-C, including at a prompt. |

## Categories

Every failure has a **category**, which selects its exit code, and a
**system**, which says who is responsible: `untaped` (a bug or the command
line), `local` (config, files, the environment), `git`, or a service such as
`awx`, `jira` or `github` (a capability may refine it, such as `awx.scm`).

| Category | Meaning | Exit |
|---|---|---|
| `usage` | Bad flags or arguments, found before any side effect | 2 |
| `config` | Local setup: missing or invalid settings, config file, CA bundle, missing tool | 4 |
| `auth` | Credentials rejected (401) | 4 |
| `permission` | Authenticated but not allowed (403) | 4 |
| `not_found` | A named thing does not exist | 1 |
| `invalid` | The remote rejected the input (400/422), or a local input file is invalid | 1 |
| `conflict` | A concurrent change, or a name already taken (409) | 1 |
| `unavailable` | Network down, timeout, 5xx, 429: retryable | 5 |
| `failed` | The operation ran and failed (a job, a test, a git command) | 1 |
| `interrupted` | Ctrl-C | 130 |

Only `unavailable` is retryable. With `--format json`, `yaml` or `pipe` (or
`UNTAPED_DIAGNOSTICS=json`), stderr reports each failure as a JSON line with
its `category`, `system`, `retryable`, `hint` and `exit_code`; failed rows of
outcome records carry the same fields in their `error`. See
[stderr diagnostics](./pipes.md#stderr-diagnostics).

## Precedence

One run can see several failures. It exits with the most severe one:

```text
130 > 2 > 4 > 5 > 1 > 3 > 0
```

So a batch where one item hit a rejected token and another a missing name
exits 4: fix the environment before you look at anything else. A batch
command that fails on some items still prints a row for every item. When both
a failure and a predicate hit happen, the failure's code wins.

Output into a closed pipe exits 0 quietly: when the reader stops early, as in
`untaped awx jobs list --format raw | head -1` or `untaped --help | head`, the
command stops writing and exits 0 with no error. A failure unrelated to that
pipe keeps its own exit code.

## Commands that exit 3

| Command | Exits 3 when |
|---|---|
| `untaped github sweep --fail-on-match` | Any repository matched the query. |
| `untaped github sweep --strict` | Any repository could not be scanned. |
| `untaped awx apply --check` | Any document would change the controller. |
| `untaped recipe apply --check` | Any target would change. |
| `untaped skills status --check` | An installed skill is outdated or no longer shipped. |
| `untaped workspace status --check` | Any repo would block `workspace archive` (uncommitted changes, stashes on its branch, unpushed commits, initialised submodules, or a missing repo cache). A repo whose git state cannot be read exits 1 instead. |

Use these in CI to tell "the check found something" (3) apart from "the tool
failed" (1), "fix the setup" (4) and "try again later" (5):

```bash
untaped github sweep --org acme --grep 'log4j' --fail-on-match --format raw --columns repo
case $? in
  0) echo "clean" ;;
  3) echo "banned pattern found" ;;
  4) echo "fix the token or config" ;;
  5) echo "GitHub unavailable; retry later" ;;
  *) echo "sweep failed" ;;
esac
```

## Commands that exit 1 on row failures

`untaped workspace run` exits 1 when the command failed, timed out or the
repo directory was missing in any repo. Usage errors exit 2.

## Codes inside records

Some rows carry a code of their own. It never becomes the process exit code:

- `untaped awx test run` reports each case's result in its rows. A case that
  did not pass carries a `failure` with its own `category` and `system` (such
  as `awx.scm` or `awx.hosts`), and the command exits with the most severe:
  - 4 for a rejected token, a credential lookup or a failed inventory update;
  - 5 for an unavailable controller, a job stuck pending or unreachable hosts;
  - otherwise 1.

  Compared with a baseline (`--compare` or `--baseline`):
  - a case that fails as it did in the baseline (`still_failing`: same
    `system`) does not count;
  - a regression, a failure the baseline cannot vouch for (`unverified`) and
    a failing new case exit 1;
  - 4 and 5 still count for any case, the `--baseline` run's included.

  With `--source-ref`, temporary copies that cannot be provisioned stop the
  run before any case, with their own code: 1 for a spec or suite to fix, 4
  when AWX refused the agent's user, 5 when it was unavailable. A copy that
  teardown cannot delete is a warning and never changes the code.

## See also

- [Command and output conventions](../conventions.md#exit-codes): how
  capability code produces these codes.
