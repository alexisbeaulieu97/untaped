---
description: "Read when setting up the AWX user, roles, token and untaped profile an agent runs awx tests as."
---

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
   specs, see `untaped agents awx source-ref`),
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

Add a profile for the agent:

```yaml
profiles:
  agent:
    awx:
      server: {url: https://aap.example.com}
      default_organization: Default
      test_timeout_seconds: 1200  # seconds a case may run before its job is cancelled
```

```console
$ untaped --profile agent awx ping
```

`ping` names the authenticated user.

Select the profile with `--profile agent` or `UNTAPED_PROFILE=agent` in the
agent's environment. A token is filed per server, so the agent's own token
for a server you also log in to comes from its environment: the user sets
`UNTAPED_AWX__TOKEN` there (for example from their password manager), which
wins over a stored login. On a server only the agent uses, the user runs
`untaped --profile agent auth login awx` instead. Without either, untaped
tries `CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN` and `AAP_TOKEN`, in that
order.

Exit 4 from a test run usually means this user lacks a role above or its
token was refused (`untaped agents awx test-results`).
