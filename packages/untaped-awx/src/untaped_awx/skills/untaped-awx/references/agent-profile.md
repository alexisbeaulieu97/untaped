# AWX agent profile

An agent that tests its own changes with `untaped awx test` runs as its own
AWX user with its own token. untaped does not limit what a token can do; AWX's
role-based access control does. Give the agent's user only the roles its
suites need, so a mistaken command fails with a permission error instead of
changing something it should not.

## On the controller

1. Create a user for the agent, for example `untaped-agent`, as neither a
   superuser nor an organization admin.
2. Grant it **Execute** on each job template its suites launch. That lets it
   read the template's launch prompts and survey, launch it, and follow and
   cancel the jobs it launched.
   For a workflow suite, grant **Execute** on the workflow job template, and
   **Approve** when its cases set `approvals:`. Without Approve, AWX refuses
   to answer its approval nodes and the case fails as `awx.credentials`.
3. Grant it **Use** on any inventory or credential its suites pass at launch
   (`inventory: !ref …`, `credentials: …`). AWX refuses a prompted resource
   the launching user cannot use.
4. For `awx test run --source-ref` (temporary copies of the repository's
   specs, see [test-suites.md](test-suites.md#temporary-test-sets---source-ref)),
   the user creates and deletes templates in the organization. Grant:
   - the organization's **Job Template Admin** role and, for workflow
     suites, **Workflow Admin**;
   - **Use** on each project, inventory, credential and instance group a
     spec names (verify on your AAP), and read access to its execution
     environment and labels;
   - **Execute** on each template AWX holds that a copied workflow's nodes
     run.

   `awx test prune` needs the same roles. Without them, provisioning fails as
   `awx.credentials` (exit 4) before any case launches.
5. Create a personal access token for the user with **Write** scope. A
   read-scope token cannot launch or cancel jobs.

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
agent's environment. `awx.token` wins over `token_command`; a profile with
neither uses `CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN` or `AAP_TOKEN`,
tried in that order.

## Running suites

Follow the skill's "Test a change" workflow with `untaped --profile agent`.
Exit 4 from AWX usually means this user lacks a role above or its token was
refused ([test-results.md](test-results.md#exit-code)).
