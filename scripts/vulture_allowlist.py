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
ReachedNode.root_ref  # ansible reach rows
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
RepoRecord.subscribed_at  # dotfiles state.yml
AppliedRecord.source_commit
AppliedRecord.applied_at
ItemRow.suggested  # dotfiles rows
StatusSummary.foreign
IssueResult.user_login  # github.issue
IssueResult.is_pull_request
JiraUser.email_address  # jira rows
IssueResult.api_url
IssueResult.priority
CommentResult.api_url
IssueDetailResult.reporter
IssueOutcome.api_url
IssueOutcome.comment_id
IssueOutcome.linked_key
TransitionResult.to_status
ProjectResult.project_type_key
BoardResult.api_url
SprintResult.start_at
SprintResult.end_at
SprintResult.goal
SprintResult.origin_board_id
BackupPruneRecord.size_bytes  # recipe backup prune rows
ProfileOutcome.previous_name  # profile rename/copy rows
ProfileOutcome.copied_from

GitHostRecord.helpers_first  # git.host rows
StoreReport.packs_median  # git.store report
StoreReport.packs_max
StoreReport.loose_objects
StoreReport.filter_ignored
StoreReport.gc_log

# The git plugin's repo store API, unused until workspace, ansible and github move onto it.
StoreError
store_report
GitHost.for_url
RepoStore.for_url
RepoStore.prefetched
RepoStore.ls_tree
RepoStore.worktree_add
RepoStore.write_worktree_config
RepoStore.filter_state
RepoStore.owned_by
Removed.freed_bytes

# untaped.testing.git: fixtures for plugins that test against the repo store.
GitRemote.spread
GitRemote.delete_branch
GitRemote.refuse_by_oid
GitRemote.drop_pack
GitRemote.packs_requested
git_remote
hostile_git_home
git_shim
trace2_events

# Read through getattr over a field table (awx application.workflow_graph).
NodeRun.workflow_job_template
NodeRun.inventory_source
NodeRun.system_job_template
NodePrompts.credentials
NodePrompts.instance_groups
NodePrompts.execution_environment
NodePrompts.job_tags
NodePrompts.skip_tags
NodePrompts.forks
NodePrompts.job_slice_count
WorkflowNodeSpec.always

# Public API for recipe hook authors.
HookHelpers.pass_
HookHelpers.fail
YamlDumpOptions.block_seq_indent  # TypedDict keys
YamlDumpOptions.explicit_start
YamlDumpOptions.explicit_end
_plain_mapping_options_are_supported  # TYPE_CHECKING type probes
_typed_options_are_supported

# SDK and repo checks that only tests call.
UiContext.confirm_action  # documented SDK
_clear_for_tests  # test hook for the composed root
core_violations  # repo lint of the core commands
ScreenRun.commands_run  # read by tests that drive screens
