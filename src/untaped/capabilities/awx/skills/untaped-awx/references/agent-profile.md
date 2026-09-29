# AWX agent profile

An AI agent that tests its own changes with `untaped awx test` (see
[test-suites.md](test-suites.md)) should run as its own AWX user with its own
token, not the user's. untaped does not limit what a token can do; AWX's
role-based access control does. Give the agent's user only the roles its
suites need, so a mistaken command fails with a permission error instead of
changing something it should not.

## On the controller

1. Create a user for the agent, for example `untaped-agent`. Do not make it a
   superuser or an organization admin.
2. Grant it **Execute** on each job template its suites launch. That lets it
   read the template's launch prompts and survey, launch it, and follow and
   cancel the jobs it launched.
3. Grant it **Use** on any inventory or credential its suites pass at launch
   (`inventory: !ref …`, `credentials: …`). AWX refuses a prompted resource
   the launching user cannot use.
4. Create a personal access token for the user with **Write** scope. A
   read-scope token cannot launch or cancel jobs.

For `--scm-branch`, each template must prompt for the branch
(`ask_scm_branch_on_launch`), which AWX only allows when its project allows
branch override.

## In untaped

Add a profile for the agent and keep the token out of `config.yml`:

```yaml
profiles:
  agent:
    awx:
      base_url: https://aap.example.com
      token_command: [pass, show, aap/untaped-agent]
      default_organization: Default
      test_timeout: 1200  # seconds a case may run before its job is cancelled
```

```bash
untaped --profile agent awx ping   # reports the authenticated user
```

Select the profile with `--profile agent` or `UNTAPED_PROFILE=agent` in the
agent's environment. A profile without `token` or `token_command` uses
`CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN` or `AAP_TOKEN` from the
environment.

## Running suites

```bash
git push -u origin HEAD
untaped --profile agent awx test run --scm-branch HEAD --format json
```

`--scm-branch HEAD` is refused until HEAD is pushed, so the jobs test the
agent's commit. Nothing prompts without a terminal, the exit code is 0 only
when every case passed, and each JSON row carries the evidence to read; see
[test-results.md](test-results.md). Exit 4 means the environment needs
fixing, not the code: read the error's `system` (`awx`: AWX refused the
agent's token or a permission its user lacks, so fix the profile or its
roles; `git`: push HEAD or fix the checkout; `local`: a missing setting such
as `awx.base_url`). Exit 5 means AAP was unavailable: retry later.
