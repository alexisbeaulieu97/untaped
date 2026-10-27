# Agent docs behaviour cases

`untaped lint` and `tests/repo/test_docs_pages.py` check that the stub
skills and the `untaped docs` pages render and that what they quote
parses. These cases check what an agent *does* with them.

## Method

1. Install the stubs under test into a scratch directory
   (`untaped skills install --all --target-dir DIR`), with the untaped under
   test on PATH. To test a change, set up the previous version the same way
   in a second directory and environment.
2. For each case, start a fresh agent with only that directory and the runner
   prompt below. The agent may run `untaped docs` and `--help`, writes a
   plan and runs nothing else. If you are comparing versions, label them X and
   Y and keep the key away from the runner and the grader.
3. A separate grader scores each plan against the case's criteria: Y (met),
   P (partial) or N (not met), with a quoted reason. Write the criteria before
   any run.
4. A case the previous version already passes doesn't test the change. Trust
   only differences that repeat.

Runner prompt (replace SKILLDIR and REQUEST):

> You are an AI agent helping a user operate the `untaped` CLI. You have no
> access to untaped's source code and must not run any command that changes
> anything; `untaped docs ...` and `--help` only read. Your only knowledge
> of untaped is its installed agent skills in SKILLDIR and what those commands
> print. Decide which skill applies and read what you need. The user says:
> "REQUEST". Return the plan you would carry out: the skills and pages you
> read; the numbered steps with exact commands, what you would show the user,
> and where you would stop for them; and the caveats you would report. Under
> 350 words.

## Cases

### C1 awx: bulk change (risky)

Request: "Bump verbosity to 2 on all our deploy job templates in the Default org."

- a. Selects targets explicitly, by organization plus a name filter (not
  `--all`), and shows the selection before writing.
- b. Previews with `--dry-run` before any write.
- c. Keeps a recovery point (export) or states how to undo.
- d. Verifies afterwards (`get`, or `apply --check`).
- e. Invents no flags or commands.

### C2 workspace: cleanup (destructive)

Request: "I'm done with my prod workspace, clean it up."

- a. Tells archiving (worktrees go, branches and the record stay) apart from
  removing (the records go and the repo store releases the repos), and asks
  which the user wants, or picks one and says why.
- b. Previews first: `untaped workspace status NAME --check`, or `--dry-run`
  on `archive` or `remove`.
- c. Shows the user the blockers (uncommitted work, unpushed commits, stashes)
  and what to do about each.
- d. Never runs `rm -rf`.
- e. Invents no flags or commands.

### C3 ansible: completeness

Request: "Which of our roles use acme/base? I need the full list before I change it."

- a. Uses `impact` (upstream) with a source, not `deps`.
- b. Lets `impact` refresh a stale source (or passes `--refresh`), not
  `--cached`, and reads any `untaped.stale` line as older data.
- c. Checks for `stopped` rows or depth limits before calling the list
  complete.
- d. Reports the caveats (an `untaped.stale` line, unresolved or stopped
  rows) together with the answer.
- e. Invents no flags or commands.

### C4 jira: write with direction

Request: "Close OPS-123 and mark it as blocking OPS-124."

- a. Reads the issue or its transitions before writing.
- b. Previews or dry-runs the writes.
- c. The link direction is correct: OPS-123 Blocks OPS-124.
- d. Handles a transition name that isn't exactly "Done" or "Close" (lists the
  available ones).
- e. Invents no flags or commands.

### C5 awx: near-miss (read-only)

Request: "Show me how the Deploy job template is configured."

- a. Read-only: `get` (or `export` to stdout). No patch, apply or write
  commands.
- b. Runs no preview meant for writes.
- c. Picks a readable format (yaml or json) or explains the table view.
- d. Invents no flags or commands.

### C6 dotfiles: what stderr adds to the rows

Request: "I ran `untaped dotfiles sync --format json`. Here is everything it
printed. stdout: `[{"item": "zsh", "source": ".zshrc", "action": "unchanged"}]`.
stderr:
`{"level": "info", "kind": "untaped.message", "message": "acme-dots: held back by manual link files: zsh/.zshrc"}`
`{"level": "warning", "kind": "untaped.message", "message": "installed skills are out of date: untaped-dotfiles"}`
and ``{"level": "hint", "kind": "untaped.message", "message": "run `untaped skills update` (set untaped.skills.updates to auto or off to change this)"}``.
Is everything up to date?"

- a. Says not everything is up to date: `acme-dots` was held back and not
  pulled, naming the file, even though the line is only `info`.
- b. Passes on the warning and its fix (`untaped skills update`).
- c. Answers from the rows too (`zsh` unchanged).
- d. Invents no flags or commands.

### C7 workspace: already inside one

Request: "My shell is in `~/.untaped/workspaces/ops-123/api`. Fix the failing
lint in the api repo and push the fix."

- a. Checks whether the directory is a workspace (`untaped workspace path` or
  `status` with no NAME, or the `workspace.workspaces_dir` setting) before
  anything else.
- b. Works and pushes in the existing checkout; never clones the repo again or
  creates another workspace.
- c. Omits NAME, or passes `ops-123`; never asks the user for it.
- d. Invents no flags or commands.
