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
   For a workflow suite, grant **Execute** on the workflow job template, and
   **Approve** on it when its cases set `approvals:` (without that role AWX
   refuses to approve or deny its approval nodes, and the case fails as
   `awx.credentials`).
3. Grant it **Use** on any inventory or credential its suites pass at launch
   (`inventory: !ref …`, `credentials: …`). AWX refuses a prompted resource
   the launching user cannot use.
4. For `awx test run --source-ref` (temporary copies of the repository's
   specs, see [test-suites.md](test-suites.md#temporary-test-sets---source-ref)),
   the user creates and deletes job templates and workflows in the
   organization: grant it the organization's **Job Template Admin** role and,
   for workflow suites, **Workflow Admin**. It also needs **Use** on each
   project, inventory and credential a spec names and on its instance groups
   (verify on your AAP), read access to its execution environment and
   labels, and **Execute** on each template AWX holds that a copied
   workflow's nodes run. `awx test prune` deletes the copies of any run in
   the organization with the same roles.
   Without them, provisioning fails as `awx.credentials` (exit 4) before any
   case launches.
5. Create a personal access token for the user with **Write** scope. A
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
agent's commit. When the change touches a template or workflow spec under
`.untaped/awx/`, run `untaped --profile agent awx test run --source-ref HEAD
--format json` instead: the suites run temporary copies of the specs at the
commit, deleted after the run. Nothing prompts without a terminal, and each
JSON row carries the evidence to read; the exit code and what to do for each
are in [test-results.md](test-results.md#exit-code). Exit 4 from AWX
usually means this user lacks a role above or its token was refused.
