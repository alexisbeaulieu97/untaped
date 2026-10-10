# untaped-github

Install it as part of `untaped`: `uv tool install 'untaped[github]'` or `pip install 'untaped[github]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped github` lists repository inventory, searches GitHub, and sweeps
many repositories for content with local `git grep`. Sweeps run over local
copies in the git plugin's repo store, so repeated questions over hundreds of
repos avoid GitHub's search limits.

## Set up

```bash
untaped auth set github
untaped config set github.default_org acme
untaped github whoami
```

For GitHub Enterprise Server, also point the profile at its API:

```bash
untaped config set github.base_url https://github.example.com/api/v3
```

With a default org,
commands given no `--org`, `--team` or `--repo` use it. The token can also
come from the GitHub CLI or `GH_TOKEN`; see
[Tokens](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/configuration.md#tokens).
`sweep` and `cache` need `git` on your `PATH`; they fetch over HTTPS with the
same token (and through `http.proxy` when set), or over SSH with your own
keys after `untaped config set github.git_protocol ssh`. Every setting is in the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#github).

## List an org's or team's repos

```bash
untaped github repos list --team acme/platform --no-fork
untaped github repos list 'svc-*' --org acme
untaped github repos list --team acme/platform --format pipe \
  | untaped workspace create platform --stdin
```

The complete inventory of the scopes you name, filtered by a name glob (or a
regex with `--regex`). Pipe it into a workspace to check the repos out.

## Search GitHub

```bash
untaped github search repos --org acme --language python
untaped github search code "BaseModel" --org acme --extension py
untaped github search issues --org acme --state open --label bug --kind pr
untaped github search users --kind org --location Montreal
```

Calls GitHub's search API: ranked, quick, and limited to 1000 results. Code
search cannot sort, use regexes, or look past the default branch; use `sweep`
for that.

## Sweep repositories for content

```bash
untaped github sweep --org acme --grep 'requests\.get\(' --path 'src/**'
untaped github sweep --org acme --grep old_api --not-grep new_api
untaped github sweep --org acme --has-file Jenkinsfile --lacks-file renovate.json
untaped github sweep --org acme --grep 'BEGIN RSA PRIVATE KEY' --fail-on-match
```

Answers "which repos still call the old API?" across every repo in a scope:
each repo is fetched into the repo store (full history, file contents only
as a grep reads them), then grepped on the refs you choose. Repeated sweeps
reuse what is stored, `--fail-on-match` gates CI, and a
`--format pipe` sweep feeds the next one's `--stdin`.

## Manage the stored repos

```bash
untaped github cache sync --team acme/platform --refs all
untaped github cache worktree acme/api --ref main
untaped github cache prune --org acme --dry-run
```

Warm the store before a batch of sweeps, check out one stored ref to read
it, and drop repos that left the org. A repo a workspace or another plugin
still uses stays when github lets go of it, and the delete says who kept it.
Deletes preview and ask first; see the skill. For clones you work in, use
[workspaces](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-workspace/README.md).

## Reference

The [packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-github/src/untaped_github/skills/untaped-github/SKILL.md) is the full reference: every workflow, safety rule and pitfall. Install it for your agent with `untaped skills install github --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)); `untaped github COMMAND --help` lists each command's options.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#github) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#github)
