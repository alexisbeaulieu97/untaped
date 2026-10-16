"""Names ``vulture`` reports as unused that are in fact used.

Decorator-registered code (cyclopts commands, pydantic validators, fixtures,
key bindings) and fixed framework signatures are ignored by ``[tool.vulture]``
in pyproject.toml. What's left is listed here as ``Owner.name  # why``. A name
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
WorkflowUsage.node_count  # a default column
Project.scm_url  # awx catalog rows: AWX's fields, typed for plugin schema
Project.allow_override
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
JiraUser.email_address  # jira rows
IssueResult.api_url
IssueResult.priority
IssueDetailResult.reporter
IssueOutcome.comment_id
IssueOutcome.linked_key
TransitionResult.to_status
ProjectResult.project_type_key
SprintResult.start_at
SprintResult.end_at
SprintResult.goal
SprintResult.origin_board_id
ProfileOutcome.previous_name  # profile rename/copy rows
ProfileOutcome.copied_from
Pack.manifest_name  # recipe.pack

GitHostRecord.helpers_first  # git.host rows

# The git plugin's repo store API with no first-party caller yet.
RepoStore.delete_refs

# untaped.testing.git: fixtures for plugins that test against the repo store.
GitRemote.spread
GitRemote.delete_branch
GitRemote.refuse_by_oid
GitRemote.drop_pack
GitRemote.packs_requested

# awx ports and readers whose first caller is awx lint (#630).
ProjectPlaybooks.playbooks
ResourceRepository.playbooks
read_resource_documents  # awx infrastructure.resources.yaml_io

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

# Public API for recipe hook authors.
HookHelpers.pass_
YamlDumpOptions.block_seq_indent  # TypedDict keys
YamlDumpOptions.explicit_start
YamlDumpOptions.explicit_end
_plain_mapping_options_are_supported  # TYPE_CHECKING type probes
_typed_options_are_supported

# SDK and repo checks that only tests call.
UiContext.confirm_action  # documented SDK
_clear_for_tests  # test hook for the composed root
ScreenRun.commands_run  # read by tests that drive screens
ProgressClock.advance  # untaped.testing: a test moves the progress clock
open_tasks  # tests check that no progress task is left open
drop_tasks  # the root conftest's cleanup of a leaked progress task

# Wire fields read by stderr's JSON readers, never by untaped itself.
StaleData.age_seconds
