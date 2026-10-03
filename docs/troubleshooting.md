# Troubleshooting

Start with `untaped doctor` (add `--online` to authenticate against each
configured service) and the exit code: each failure's category says who must
act. This page maps common symptoms to the section that explains them.

| Symptom | Where to look |
|---|---|
| Exit 4 (`config`, `auth` or `permission`) | The environment needs fixing, not your command: [categories](./reference/exit-codes.md#categories). |
| Exit 5 (`unavailable`) | Temporary; retry later: [exit codes](./reference/exit-codes.md). |
| A token is rejected (HTTP 401) or not found | Where tokens come from and in what order: [Tokens](./configuration.md#tokens). |
| A certificate error on a self-hosted service | A CA bundle rather than disabling checks: [TLS](./configuration.md#tls-and-shared-ui-settings). |
| `config.yml` or `state.yml` is refused as newer | Written by a newer `untaped`: [File format](./configuration.md#file-format). |
| A setting is ignored, or `doctor` shows `unknown-keys` | A section at the wrong level: [File and layout](./configuration.md#file-and-layout). |
| A warning that a capability is quarantined | `untaped capabilities` and `untaped doctor` name the reason: [How composition works](./composition.md). |
| A warning that installed skills are out of date | [Keep installed skills up to date](./skills.md#keep-installed-skills-up-to-date). |
| A script cannot parse an error | Use the JSON diagnostics on stderr: [stderr diagnostics](./scripting.md#stderr-diagnostics). |

To see what a command actually sent and ran, add `--verbose`: see
[Debug logs](./configuration.md#debug-logs). Capability-specific failures
(cloning private repos, an AWX job that will not launch, a recipe hook that
fails) are in each capability's skill (its Setup and Pitfalls, and the
references it links).
