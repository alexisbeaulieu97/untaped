"""Names ``vulture`` reports as unused that are in fact used.

Decorator-registered code (pydantic validators, fixtures, key bindings) and
fixed framework signatures are ignored by ``[tool.vulture]`` in
pyproject.toml. What's left is listed here as ``Owner.name  # why``. A name
listed here counts as used everywhere, so keep entries specific and remove a
line once its code is gone.
"""

# ruff: noqa

# Output record fields: emitted whole (emit/model_dump) or read by users.
DependencyMatch.root_repo  # ansible.dependency_match
DependencyMatch.root_ref
DependencyMatch.input_kind
DependencyMatch.input_id
DependencyMatch.input_name
FailureEvidence.unreachable_hosts  # awx test results
FailureEvidence.changed_tasks
CaseResult.rerun_job_id
CaseResult.hosts_truncated
Resource.apiVersion  # written to saved resource YAML
PingStatus.active_node  # awx.status
PingStatus.install_uuid
WorkflowNode.workflow_job_template  # awx workflow-nodes rows
WorkflowNode.summary_fields
Project.scm_url  # awx catalog rows: AWX's fields, typed for plugin schema
JobTemplate.scm_url  # job-templates list/get --with-scm
JobTemplate.project_allow_override
ActionOutcome.target_kind  # awx.launch_outcome / awx.sync_outcome
ActionOutcome.target_name
ActionOutcome.job_failed  # dumped as `failed`
ApplyOutcomeRow.fields_changed  # awx.apply_outcome
RepoRecord.subscribed_at  # dotfiles state.yml
AppliedRecord.source_commit
AppliedRecord.applied_at
ItemRow.suggested  # dotfiles rows
IssueResult.user_login  # github.issue
IssueResult.is_pull_request
User.email_address  # jira rows
IssueOutcome.comment_id
IssueOutcome.linked_key
Transition.to_status
Project.project_type_key
Sprint.start_at
Sprint.end_at
Sprint.goal
Sprint.origin_board_id
ProfileOutcome.previous_name  # profile rename/copy rows
ProfileOutcome.copied_from
Pack.manifest_name  # recipe.pack

GitHostRecord.helpers_first  # git.host rows

# The git plugin's repo store API with no first-party caller yet (lock_worktree and
# repair_worktree too, kept by their same-named helpers, until #773).
AnchorId.ino  # compared whole: a folder's filesystem identity
RepoStore.delete_refs
RepoStore.worktrees_under  # workspace releases a missing project's worktrees (#773)

# untaped.sdk.testing.git: fixtures for plugins that test against the repo store.
GitRemote.delete_branch
GitRemote.refuse_by_oid
GitRemote.drop_pack
GitRemote.packs_requested

# awx lint's typed views of awx.resources, for rule authors (untaped_awx.api).
ControllerFacts.last_job_failed
SurveyQuestionDoc.question_name
ScheduleDoc.rrule
ProjectDoc.scm_type
JobTemplateDoc.ask_variables_on_launch
JobTemplateDoc.ask_limit_on_launch
JobTemplateDoc.ask_inventory_on_launch
JobTemplateDoc.ask_credential_on_launch
JobTemplateDoc.ask_scm_branch_on_launch
JobTemplateDoc.ask_tags_on_launch
JobTemplateDoc.ask_skip_tags_on_launch
JobTemplateDoc.ask_verbosity_on_launch
JobTemplateDoc.ask_diff_mode_on_launch
JobTemplateDoc.ask_job_type_on_launch
JobTemplateDoc.ask_execution_environment_on_launch
JobTemplateDoc.ask_labels_on_launch
JobTemplateDoc.ask_forks_on_launch
JobTemplateDoc.ask_job_slice_count_on_launch
JobTemplateDoc.ask_timeout_on_launch
JobTemplateDoc.ask_instance_groups_on_launch
WorkflowJobTemplateDoc.ask_variables_on_launch
WorkflowJobTemplateDoc.ask_inventory_on_launch
WorkflowJobTemplateDoc.ask_scm_branch_on_launch
WorkflowJobTemplateDoc.ask_limit_on_launch
WorkflowJobTemplateDoc.ask_labels_on_launch
WorkflowJobTemplateDoc.ask_tags_on_launch
WorkflowJobTemplateDoc.ask_skip_tags_on_launch

# Read through getattr over a field table (awx application.resources.workflow_graph).
NodeRun.inventory_source
NodeRun.system_job_template
NodePrompts.instance_groups
NodePrompts.execution_environment
NodePrompts.job_tags
NodePrompts.skip_tags
NodePrompts.forks
NodePrompts.job_slice_count
WorkflowNodeSpec.always

# Launch options the LAUNCH_FLAGS table reads by name (awx commands.resource_actions),
# and the prompts fields of a node's and a lint view's launch values.
Launch.launch_inventory
Launch.job_tag
Launch.skip_tag
Launch.verbosity
Launch.diff_mode
NodePrompts.verbosity
NodePrompts.diff_mode

# Public API for recipe hook authors.
HookHelpers.pass_
YamlDumpOptions.block_seq_indent  # TypedDict keys
YamlDumpOptions.explicit_start
YamlDumpOptions.explicit_end
_plain_mapping_options_are_supported  # TYPE_CHECKING type probes
_typed_options_are_supported

# SDK and repo checks that only tests call.
StateRows.per_project  # documented SDK
_.parent_app  # cyclopts' resolve() signature, overridden by the lazy plugin mount
DataFile.copy_to  # documented SDK
_clear_for_tests  # test hook for the composed root
ScreenRun.commands_run  # read by tests that drive screens
Ran.rows_as  # untaped.sdk.testing: a test reads its rows typed
ScriptedTerminal.screens  # untaped.sdk.testing: a test scripts its terminal
ScriptedTerminal.selects
ScriptedTerminal.interrupts
CliResult.exception  # untaped.sdk.testing: a test reads what a command raised
open_tasks  # tests check that no progress task is left open
drop_tasks  # the root conftest's cleanup of a leaked progress task

# Wire fields read by stderr's JSON readers, never by untaped itself.
Stale.age_seconds

# Data file fields kept for the record and shown by `backups get`, never read by name.
BackupEntry.before_hash

# Error systems the SDK names, which plugins still spell as strings.
System.LOCAL
System.GIT
System.HTTP

# Blocks on a plugin class: composition reads them from the class, never by name.
Core.config_permissions
Core.unknown_keys
Core.token_store
Dotfiles.clones
Git.repo_store
WorkspacePlugin.workspaces
WorkspacePlugin.checkouts
Ansible.source_data
RecipePlugin.uv
LintPlugin.ignore_ids

# Enum members read by value from manifests and the state file.
Policy.ONCE  # dotfiles: `policy: once`

# The contract answers' views: SDK API, read by the shelf example and third-party owners.
Answers.errored
