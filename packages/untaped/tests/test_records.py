"""Behavioural tests for the shared record bases in ``untaped.records``."""

from __future__ import annotations

import importlib
import json
import pkgutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    Secret,
    SecretStr,
    ValidationError,
    computed_field,
    field_serializer,
    model_serializer,
)

from untaped.errors import ConfigError, HttpStatusError, HttpTransportError
from untaped.output.diagnostic import (
    Debug,
    DeprecationNotice,
    Message,
    Progress,
    Pruned,
    StaleData,
    WireModel,
)
from untaped.output.sink import (
    Diagnostics,
    current_sink,
    diagnostics_scope,
    failure_exit_code,
)
from untaped.records import (
    CheckRecord,
    DuplicateKindError,
    Failure,
    ForeignKindError,
    OutcomeRecord,
    Record,
    TargetRecord,
    UtcTimestamp,
    kind_of,
    record_kinds,
    record_model,
)


class _Stamped(BaseModel):
    scanned_at: UtcTimestamp


class _CloneOutcome(OutcomeRecord, TargetRecord):
    repo: str


def test_utc_timestamp_renders_rfc3339_z_in_full() -> None:
    eastern = timezone(timedelta(hours=-5))
    record = _Stamped(scanned_at=datetime(2026, 1, 2, 3, 4, 5, 678, tzinfo=eastern))

    assert record.scanned_at == datetime(2026, 1, 2, 8, 4, 5, 678, tzinfo=UTC)
    assert record.model_dump(mode="json") == {"scanned_at": "2026-01-02T08:04:05.000678Z"}


def test_utc_timestamp_keeps_microseconds_through_a_json_round_trip() -> None:
    record = _Stamped(scanned_at=datetime(2026, 1, 2, 3, 4, 5, 250_000, tzinfo=UTC))

    dumped = json.dumps(record.model_dump(mode="json", round_trip=True))

    assert _Stamped.model_validate_json(dumped, strict=True) == record


def test_utc_timestamp_takes_naive_values_and_strings_as_utc() -> None:
    naive = _Stamped(scanned_at=datetime(2026, 1, 2, 3, 4, 5))
    parsed = _Stamped.model_validate({"scanned_at": "2026-01-02T03:04:05+0000"})

    assert naive.model_dump(mode="json") == {"scanned_at": "2026-01-02T03:04:05Z"}
    assert parsed.scanned_at == naive.scanned_at


def test_outcome_and_target_bases_compose(tmp_path: Path) -> None:
    row = _CloneOutcome(action="failed", target_path=tmp_path, repo="a/b")

    assert row.failed is True
    assert row.model_dump(mode="json") == {
        "action": "failed",
        "target_path": str(tmp_path),
        "repo": "a/b",
    }
    assert _CloneOutcome(action="skipped", target_path=tmp_path, repo="a/b").failed is False


def test_target_path_must_be_absolute() -> None:
    with pytest.raises(ValidationError, match="target_path must be absolute"):
        TargetRecord(target_path=Path("relative/dir"))


def test_records_are_frozen_and_reject_unknown_fields() -> None:
    check = CheckRecord(status="pass")
    with pytest.raises(ValidationError):
        check.status = "fail"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        CheckRecord.model_validate({"status": "ok"})
    with pytest.raises(ValidationError):
        CheckRecord.model_validate({"status": "pass", "extra": 1})


class _BranchOutcome(OutcomeRecord, TargetRecord):
    repo: str
    action: str
    branch: str


def test_own_fields_come_before_inherited_base_fields(tmp_path: Path) -> None:
    row = _CloneOutcome(action="cloned", target_path=tmp_path, repo="a/b")

    assert list(row.model_dump()) == ["repo", "action", "target_path"]
    assert list(row.model_dump(mode="json")) == ["repo", "action", "target_path"]
    assert row.model_dump_json().startswith('{"repo":')


class _SyncOutcome(OutcomeRecord, TargetRecord):
    repo: str
    fetched: int
    detail: str | None = None


def test_an_inherited_action_follows_the_identifying_field(tmp_path: Path) -> None:
    row = _SyncOutcome(action="synced", target_path=tmp_path, repo="a/b", fetched=3)

    assert list(row.model_dump(mode="json")) == [
        "repo",
        "action",
        "fetched",
        "detail",
        "target_path",
    ]


class _ReversedBases(TargetRecord, OutcomeRecord):
    repo: str


class _Bare(OutcomeRecord):
    pass


class _Named(OutcomeRecord):
    id: int
    name: str
    detail: str | None = None


def test_action_placement_across_record_shapes(tmp_path: Path) -> None:
    reversed_row = _ReversedBases(action="cloned", target_path=tmp_path, repo="a/b")
    assert list(reversed_row.model_dump(mode="json")) == ["repo", "action", "target_path"]
    bare = _Bare(action="failed", error=Failure.from_exception(ConfigError("x")))
    assert list(bare.model_dump(mode="json")) == ["action", "error"]
    named = _Named(id=1, name="n", action="created")
    assert list(named.model_dump(mode="json")) == ["id", "name", "action", "detail"]


def test_redeclared_base_fields_keep_the_subclass_position(tmp_path: Path) -> None:
    row = _BranchOutcome(repo="a/b", action="updated", branch="main", target_path=tmp_path)

    assert list(row.model_dump(mode="json")) == ["repo", "action", "branch", "target_path"]


def test_a_failed_row_carries_the_error_of_the_exception_that_failed_it(tmp_path: Path) -> None:
    cause = HttpStatusError("HTTP 503", status_code=503, url="https://h/x", system="awx")

    row = _CloneOutcome(
        action="failed", target_path=tmp_path, repo="a/b", error=Failure.from_exception(cause)
    )

    assert row.model_dump(mode="json")["error"] == {
        "category": "unavailable",
        "system": "awx",
        "retryable": True,
        "message": "HTTP 503 for https://h/x",
        "hint": None,
        "exit_code": 5,
        "details": {"status": 503, "url": "https://h/x"},
    }


def test_rows_without_an_error_omit_the_field(tmp_path: Path) -> None:
    row = _CloneOutcome(action="cloned", target_path=tmp_path, repo="a/b")

    assert "error" not in row.model_dump(mode="json")
    assert "error" not in row.model_dump()


def test_error_info_splits_the_hint_off_an_overridden_message() -> None:
    cause = ConfigError("rejected\nhint: run `untaped config set awx.token --prompt`")

    info = Failure.from_exception(cause, message="token <redacted> rejected")

    assert (info.category, info.system, info.retryable) == ("config", "local", False)
    assert info.message == "token <redacted> rejected"
    info = Failure.from_exception(cause)
    assert info.message == "rejected"
    assert info.hint == "run `untaped config set awx.token --prompt`"


def test_error_info_of_an_unexpected_exception_is_a_failed_untaped_error() -> None:
    info = Failure.from_exception(KeyError("id"))

    assert (info.category, info.system, info.message) == ("failed", "untaped", "'id'")


def test_building_a_failure_is_pure_and_emitting_it_counts() -> None:
    with diagnostics_scope():
        info = Failure.from_exception(HttpTransportError("down", system="awx"), message="redacted")

        assert (info.category, info.system, info.message) == ("unavailable", "awx", "redacted")
        assert failure_exit_code() == 1

        Diagnostics().failure(info)
        assert failure_exit_code() == 5


def test_emitting_a_failure_counts_like_diag_failure() -> None:
    info = Failure.from_exception(ConfigError("rejected", category="auth"))
    with diagnostics_scope():
        current_sink().emit(info)
        assert failure_exit_code() == 4


def test_an_interrupt_is_interrupted_everywhere() -> None:
    info = Failure.from_exception(KeyboardInterrupt())
    with diagnostics_scope():
        Diagnostics().failure(info)
        assert (info.category, failure_exit_code()) == ("interrupted", 130)


# ---- kinds -----------------------------------------------------------------


class _Widget(Record, kind="acme-tools.widget"):
    name: str


class _WidgetSummary(Record, kind="acme-tools.widget.summary"):
    total: int


class _SpecialWidget(_Widget):
    colour: str


def test_a_record_declares_its_kind_and_is_registered_under_it() -> None:
    assert kind_of(_Widget) == "acme-tools.widget"
    assert record_model("acme-tools.widget") is _Widget
    assert record_kinds()["acme-tools.widget.summary"] is _WidgetSummary
    assert "kind" not in _Widget.model_fields


def test_bases_and_subclasses_without_kind_are_kind_less() -> None:
    assert [kind_of(base) for base in (Record, OutcomeRecord, TargetRecord, CheckRecord)] == [
        None,
        None,
        None,
        None,
    ]
    assert kind_of(_SpecialWidget) is None
    assert record_model("acme-tools.nothing") is None


@pytest.mark.parametrize(
    "kind",
    ["acme_tools.widget", "acme.widget.extra", "acme.summary", "Acme.widget", "acme"],
)
def test_a_kind_outside_the_grammar_fails_at_definition(kind: str) -> None:
    with pytest.raises(ValueError, match="invalid record kind"):

        class _Bad(Record, kind=kind):
            name: str


def test_two_models_declaring_one_kind_is_duplicate_kind() -> None:
    with pytest.raises(DuplicateKindError, match=r"kind 'acme-tools\.widget' is declared by both"):

        class _Rival(Record, kind="acme-tools.widget"):
            name: str

    assert record_model("acme-tools.widget") is _Widget


def test_a_plugin_package_declares_kinds_under_its_own_name() -> None:
    class _Own(Record, kind="acme-tools.kit_part"):
        __module__ = "untaped_acme_tools.records"
        name: str

    assert record_model("acme-tools.kit_part") is _Own


@pytest.mark.parametrize(
    ("module", "kind", "declarer", "fixes"),
    [
        (
            "untaped_acme_tools.records",
            "acme.gizmo",
            "plugin 'acme-tools'",
            "name it acme-tools.gizmo, or ship it in the package untaped_acme",
        ),
        (
            "untaped_awx.records",
            "untaped.gizmo",
            "plugin 'awx'",
            "name it awx.gizmo, or ship it in the package untaped",
        ),
        (
            "untaped.records_extra",
            "acme-tools.gizmo",
            "core",
            "name it untaped.gizmo, or ship it in the package untaped_acme_tools",
        ),
    ],
)
def test_a_kind_under_another_plugins_name_is_foreign_kind(
    module: str, kind: str, declarer: str, fixes: str
) -> None:
    with pytest.raises(ForeignKindError, match=f"belongs to {declarer}; ") as caught:

        class _Foreign(Record, kind=kind):
            __module__ = module
            name: str

    assert str(caught.value).endswith(fixes)
    assert caught.value.reason == "foreign-kind"
    assert record_model(kind) is None


def test_a_model_outside_plugin_packages_is_nobodys() -> None:
    class _Anywhere(Record, kind="forge.gizmo"):
        name: str

    assert record_model("forge.gizmo") is _Anywhere


def test_the_same_class_defined_again_replaces_itself() -> None:
    def define() -> type[Record]:
        class _Reloaded(Record, kind="acme-tools.reloaded"):
            name: str

        return _Reloaded

    first, second = define(), define()

    assert record_model("acme-tools.reloaded") is second
    assert kind_of(first) == kind_of(second) == "acme-tools.reloaded"


class _Page[T](Record, kind="acme-tools.page"):
    items: list[T]


def test_a_parametrized_generic_record_has_its_generics_kind() -> None:
    assert kind_of(_Page[int]) == "acme-tools.page"


# ---- the round-trip check ----------------------------------------------------


class _Nested(BaseModel):
    token: SecretStr


def _join(value: list[str]) -> str:
    return ",".join(value)


@pytest.mark.parametrize(
    ("annotation", "default", "why"),
    [
        (SecretStr, ..., "SecretStr dumps masked"),
        (list[SecretStr | None], ..., "SecretStr dumps masked"),
        (Secret[str], ..., "Secret dumps masked"),
        (_Nested, ..., "SecretStr dumps masked"),
        (str, Field(exclude=True), r"Field\(exclude=True\)"),
        (str, Field(serialization_alias="url"), "dumps as 'url'"),
        (str, Field(alias="Value"), "dumps as 'value'"),
        (str, Field(validation_alias="url"), "dumps as 'value'"),
        (int | None, Field(default=None, exclude_if=lambda v: not v), "exclude_if may only"),
        (int, Field(default=0, exclude_if=lambda v: v == 0), "exclude_if may only"),
        (Annotated[list[str], PlainSerializer(_join)], ..., "PlainSerializer _join"),
        ("_Later", ..., "it refers to a type not defined yet"),
    ],
)
def test_a_field_that_loses_data_in_a_json_round_trip_fails_at_definition(
    annotation: object, default: object, why: str
) -> None:
    namespace: dict[str, object] = {"__annotations__": {"value": annotation}}
    if default is not ...:
        namespace["value"] = default
    with pytest.raises(TypeError, match=f"_Lossy(\\.value(\\.token)?)?: {why}.*would lose data"):
        type("_Lossy", (Record,), namespace)


def test_serializers_and_computed_fields_fail_at_definition() -> None:
    with pytest.raises(TypeError, match="_Joined: field_serializer on tags may not invert"):

        class _Joined(Record):
            tags: list[str]

            @field_serializer("tags")
            def _join(self, value: list[str]) -> str:
                return ",".join(value)

    class _Flat(BaseModel):
        name: str

        @model_serializer
        def _dump(self) -> str:
            return self.name

    with pytest.raises(TypeError, match="value: a model_serializer may not invert"):

        class _Holder(Record):
            value: _Flat

    with pytest.raises(TypeError, match="computed field owner does not read back"):

        class _Repo(Record):
            full_name: str

            @computed_field  # type: ignore[prop-decorator]
            @property
            def owner(self) -> str:
                return self.full_name.split("/")[0]


class _Tree(BaseModel):
    children: list[_Tree] = []


class _RoundTrips(Record, kind="acme-tools.round_trips"):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_by_name=True)

    named: str = Field(alias="Named")
    chosen: str | None = Field(default=None, validation_alias=AliasChoices("chosen", "pick"))
    by_name: str = Field(default="", validation_alias="byName")
    at: UtcTimestamp
    where: Path
    error: Failure | None = None
    tree: _Tree = _Tree()


class _ByAlias(Record, kind="acme-tools.by_alias"):
    model_config = ConfigDict(frozen=True, extra="forbid", serialize_by_alias=True)

    html_url: str = Field(alias="url")


def test_aliases_validation_accepts_are_allowed() -> None:
    _assert_round_trips(
        _RoundTrips(
            Named="n",
            pick="c",
            byName="b",
            at=datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC),
            where=Path("/tmp/x"),
            error=Failure.from_exception(ConfigError("x")),
            tree=_Tree(children=[_Tree()]),
        )
    )
    _assert_round_trips(_ByAlias(url="https://h/x"))


def test_a_deferred_build_is_not_an_unresolved_type() -> None:
    class _Deferred(Record):
        model_config = ConfigDict(frozen=True, extra="forbid", defer_build=True)

        name: str

    _assert_round_trips(_Deferred(name="n"))


def test_an_alias_validation_is_told_to_ignore_fails_at_definition() -> None:
    with pytest.raises(TypeError, match=r"_NoAlias\.html_url: dumps as 'url'"):

        class _NoAlias(Record):
            model_config = ConfigDict(
                frozen=True,
                extra="forbid",
                serialize_by_alias=True,
                validate_by_alias=False,
                validate_by_name=True,
            )

            html_url: str = Field(alias="url")


def _assert_round_trips(record: BaseModel) -> None:
    """Read back the dump ``emit`` writes, in strict JSON mode."""
    dumped = json.dumps(record.model_dump(mode="json"))
    assert type(record).model_validate_json(dumped, strict=True) == record


#: Core's and the installed plugins' import packages (a test's fake plugin is
#: a single module, not a package).
_SHIPPED = frozenset(
    info.name
    for info in pkgutil.iter_modules()
    if info.ispkg and (info.name == "untaped" or info.name.startswith("untaped_"))
)


def _is_shipped(module: str) -> bool:
    return module.partition(".")[0] in _SHIPPED


def _import_every_shipped_module() -> None:
    """Import every module of core and the installed plugins, so all their kinds register."""
    for top in sorted(_SHIPPED):
        package = importlib.import_module(top)
        for info in pkgutil.walk_packages(package.__path__, f"{top}."):
            if not info.name.endswith(".__main__"):
                importlib.import_module(info.name)


def _samples() -> dict[str, WireModel]:
    from untaped.config.models import SettingOutcome, SettingRow
    from untaped.management.plugins import ContractRow
    from untaped.management.records import (
        AuthOutcome,
        ConfigMigrationOutcome,
        DoctorCheckRow,
        FixOutcome,
        InstalledSkillRow,
        MigrationPreviewRow,
        PluginCheckRow,
        RegisterOutcome,
        ScaffoldOutcome,
        SetupStep,
        SkillOutcome,
        SkillRow,
        TokenSource,
    )
    from untaped.plugin_records import Plugin
    from untaped.plugins.registry import MigrationOutcome
    from untaped.profile.models import ProfileOutcome, ProfileRow

    rows: list[WireModel] = [
        Message(level="warning", message="w", hint="h"),
        Failure.from_exception(ConfigError("x")),
        Debug(logger="untaped.http", message="GET"),
        Progress(
            message="Syncing · fetching refs 1/2 (3s)",
            label="Syncing",
            phase="fetching refs",
            done=1,
            total=2,
            elapsed=3.0,
            state="running",
        ),
        StaleData.describe(
            plugin="hello",
            subject="shelf",
            cache="hello.shelf_books",
            key="k",
            refreshed_at=datetime(2026, 10, 9, tzinfo=UTC),
            now=datetime(2026, 10, 10, tzinfo=UTC),
            max_age=timedelta(hours=1),
            error=Failure.from_exception(ConfigError("x")),
        ),
        Pruned(
            message=(
                "removed layout 4 of store hello.shelf at /x/shelf.v4.db (1 B): nothing holds it"
            ),
            store="hello.shelf",
            layout=4,
            path="/x/shelf.v4.db",
            bytes=1,
        ),
        RegisterOutcome(
            action="registered",
            holder="install:3f9a1c2e8b7d",
            environment="/x/site-packages",
            version="11.1.0",
            layouts=["hello.shelf@4"],
        ),
        DeprecationNotice(
            id="cli untaped hello old",
            removed_in=12,
            distribution="untaped-hello",
            replacement="untaped hello new",
            hint="h",
            message="`untaped hello old` is deprecated",
        ),
        SettingRow(key="k", value={"a": [1]}, default=None, source="default", profile=None),
        SettingOutcome(key="k", profile="default", action="updated"),
        ProfileRow(name="default", active=True, keys=3),
        ProfileOutcome(name="work", action="renamed", previous_name="old"),
        ContractRow(
            contract="workspace.repo_source",
            method="repos",
            owner="workspace",
            stability="experimental",
            providers=["github", "gitlab"],
            ranked=["github"],
        ),
        PluginCheckRow(
            plugin="bin", check="live", title="rack.item_source.items", status="pass", detail="2"
        ),
        ScaffoldOutcome(action="created", target_path=Path("/tmp/untaped-bin/pyproject.toml")),
        MigrationOutcome(id="github.cache", action="moved", detail="3 repos"),
        AuthOutcome(
            section="github",
            profile="default",
            store=None,
            action="failed",
            detail="x",
            error=Failure.from_exception(ConfigError("x")),
        ),
        TokenSource(profile="default", section="github", source="$GITHUB_TOKEN"),
        ConfigMigrationOutcome.model_validate(
            {"profile": "default", "from": "a.b", "to": "a.c", "action": "renamed", "detail": ""}
        ),
        DoctorCheckRow(
            check="config",
            plugin="untaped",
            status="warn",
            title="t",
            detail="d",
            fix=["config", "migrate"],
            automatic=True,
        ),
        FixOutcome(fix=["config", "migrate"], checks=["config"], action="fixed", detail="d"),
        MigrationPreviewRow(id="github.cache", action="move", source="/a", destination="/b"),
        SetupStep(step="profile", plugin="untaped", state="todo", detail="d", run=[], by="agent"),
        SkillRow(name="untaped-hello", description="d", source="/s"),
        InstalledSkillRow(
            name="untaped-hello",
            target="claude",
            scope="user",
            state="current",
            target_path=Path("/h/.claude/skills/untaped-hello"),
        ),
        SkillOutcome(
            name="untaped-hello",
            target="claude",
            scope="user",
            target_path=Path("/h/.claude/skills/untaped-hello"),
            action="updated",
        ),
        Plugin(
            name="acme",
            status="ready",
            distribution="untaped-acme",
            version="1.0.0",
            package="untaped_acme",
            source_dir=Path("/src/untaped_acme"),
            tests_dir=Path("/tests"),
        ),
    ]
    if "untaped_git" in _SHIPPED:  # the core-only job installs no plugin
        from untaped_git.domain.records import GitHostRecord, StoreReport, TreeEntry

        rows += [
            GitHostRecord(host="github.com", plugins=["github"], credential=True),
            StoreReport(store_dir="/s", repos=1, size_bytes=2, filter_ignored={"git.example": 1}),
            TreeEntry(mode="100644", type="blob", oid="0" * 40, path="a.yml"),
        ]
    if "untaped_workspace" in _SHIPPED:
        from untaped_workspace.api import Repo
        from untaped_workspace.records import (
            ArchiveOutcome,
            RemoveOutcome,
            RepoOutcome,
            ResolveOutcome,
            RunOutcome,
            StatusRow,
            WorkspaceRow,
        )

        target = Path("/w/fix/api")
        rows += [
            Repo(
                name="acme/api",
                url="https://github.com/acme/api.git",
                source={"plugin": "github", "kind": "github.repo", "record": {"x": 1}},
            ),
            WorkspaceRow(
                name="fix",
                repos=1,
                created_at=datetime(2026, 10, 1, tzinfo=UTC),
                target_path=Path("/w/fix"),
            ),
            RepoOutcome(
                workspace="fix", repo="acme/api", dir="api", action="created", target_path=target
            ),
            StatusRow(
                workspace="fix",
                repo="acme/api",
                dir="api",
                branch="fix",
                base="main",
                read_only=False,
                state="ok",
                blockers=("2 modified",),
                target_path=target,
            ),
            ArchiveOutcome(workspace="fix", repo="acme/api", action="removed", target_path=target),
            RemoveOutcome(
                workspace="fix",
                repo="acme/api",
                action="failed",
                target_path=target,
                error=Failure.from_exception(ConfigError("x")),
            ),
            ResolveOutcome(
                workspace="fix",
                repo="acme/api",
                dir="api",
                action="unchanged",
                url="https://github.com/acme/api.git",
                target_path=target,
            ),
            RunOutcome(
                workspace="fix",
                repo="acme/api",
                dir="api",
                action="ran",
                returncode=0,
                duration_s=0.5,
                target_path=target,
            ),
        ]
    if "untaped_github" in _SHIPPED:
        from untaped_github.records import (
            CodeResult,
            CorpusRepoResult,
            CorpusSyncOutcome,
            GithubRepo,
            GithubUser,
            IssueResult,
            RepoSweepOutcome,
            SweepFile,
            SweepMatch,
            UserResult,
            WorktreeResult,
        )

        url = "https://github.com/acme/api"
        rows += [
            GithubRepo(full_name="acme/api", private=False, pushed_at="2026-10-10T00:00:00Z"),
            CorpusRepoResult(full_name="acme/api", ref="main", path="/s/acme/api.git"),
            RepoSweepOutcome(full_name="acme/api", hits={"a.yml": 2}, owners=("@acme/team",)),
            GithubUser(login="octo", id=1, name=None, email="octo@example.com"),
            CodeResult(name="a.yml", path="ci/a.yml", sha="0" * 40, repo="acme/api", url=url),
            IssueResult(number=1, id=2, title="t", state="open", url=url, user_login=None),
            UserResult(id=1, login="octo", type="User", url="https://github.com/octo"),
            SweepFile(repo="acme/api", path="a.yml", refs=("main",), hits=2),
            SweepMatch(repo="acme/api", refs=("main",), path="a.yml", line=3, text="x: 1"),
            CorpusSyncOutcome(
                repo="acme/api",
                action="failed",
                fetched_at=datetime(2026, 10, 10, tzinfo=UTC),
                detail="x",
                error=Failure.from_exception(ConfigError("x")),
            ),
            WorktreeResult(
                target_path=Path("/s/wt/api"), path="/s/wt/api", repo="acme/api", ref="main"
            ),
        ]
    if "untaped_recipe" in _SHIPPED:
        from untaped_recipe import records as recipe

        failure = Failure.from_exception(ConfigError("x"))
        rows += [
            recipe.ApplyOutcome(
                target_path=Path("/w/acme"),
                action="failed",
                files_changed=1,
                warnings=["w"],
                detail="d",
                inputs={"owner": "acme", "n": [1, None]},
                recipe="acme/ci",
                error=failure,
            ),
            recipe.AddOutcome(name="acme", action="created", source="/src/acme"),
            recipe.SyncOutcome(name="acme", action="failed", detail="d", error=failure),
            recipe.RemoveOutcome(name="acme", action="removed", rev="v1", commit="abc"),
            recipe.PruneOutcome(id="b1", size_bytes=3, action="deleted"),
            recipe.RestoreOutcome(id="b1", files=2, action="restored"),
            recipe.Backup(
                id="b1",
                created_at=datetime(2026, 10, 10, tzinfo=UTC),
                recipe="demo",
                path="/l/b1",
                inputs={},
                files=[{"target": "/w", "relative_path": "a.yml", "backup_file": None}],
            ),
            recipe.InstalledRecipe(
                pack="acme",
                name="ci",
                ref="acme/ci",
                description="d",
                inputs=[
                    recipe.RecipeInput(
                        name="owner",
                        type="str",
                        required=False,
                        default="acme",
                        description="",
                        sensitive=False,
                    )
                ],
                steps=[recipe.RecipeStep(type="copy", files=["a"], globs=[], exclude=[], hook="")],
                hooks=[],
                path="/l/acme/ci.yml",
            ),
            recipe.Hook(pack="acme", name="h", ref="acme/h", module="acme.h", path="/h.py"),
            recipe.Pack(
                name="acme",
                project="acme-pack",
                version="1.0",
                path="/l/acme",
                source="/src/acme",
                rev="",
                commit="",
                recipes=[recipe.PackRecipe(name="ci", description="d")],
                hooks=1,
            ),
            recipe.HookRun(hook="h", kind="validate", target="/w", status="pass", message=""),
            recipe.LibraryCheck(name="acme", type="pack", status="pass", path="/l/acme"),
            recipe.GoldenCase(
                pack="acme", recipe="ci", case="c", status="error", detail="d", error=failure
            ),
        ]
    if "untaped_ansible" in _SHIPPED:
        from untaped_ansible.records import (
            AliasOutcome,
            DependencyMatch,
            Dependent,
            ReachedDependency,
            ReachedDependent,
            Source,
            SourceAlias,
            SourceOutcome,
            SourceStatus,
            VariableUseHit,
        )

        reached = {
            "repo": "acme/base",
            "ref": "main",
            "unresolved": None,
            "declared_ref": "v1",
            "declared_in": "roles/requirements.yml",
            "depth": 1,
            "path": ["acme/site@main", "acme/base@main"],
            "root_ref": "main",
            "stopped": "depth",
        }
        rows += [
            ReachedDependency.model_validate(reached),
            ReachedDependent.model_validate(reached),
            DependencyMatch(
                root_repo="acme/site",
                root_ref="main",
                repo="acme/base",
                declared_ref=None,
                declared_in="meta/main.yml",
                path=["acme/site@main", "acme/base"],
                input_kind="awx.job_template",
                input_id=7,
                input_name="deploy",
            ),
            Source(
                name="prod",
                orgs=["acme"],
                teams=["acme/platform"],
                repos=[],
                dependency_paths=[],
                ref_kinds=["heads"],
                ref_patterns=[],
                ref_scan_default="all",
            ),
            SourceAlias(alias="common", repo="acme/common"),
            AliasOutcome(action="created", alias="common", repo="acme/common"),
            SourceOutcome(
                action="failed",
                name="prod",
                changes=["failed to release acme/web"],
                error=Failure.from_exception(ConfigError("x")),
            ),
            SourceStatus(
                source="prod",
                source_key="source:prod",
                state="stale",
                configured=True,
                scanned_at="2026-10-10T00:00:00Z",
                repos=2,
                refreshed_at="2026-10-10T00:00:00Z",
                stale=True,
                max_age_seconds=3600,
            ),
        ]
        rows.append(
            VariableUseHit(
                variable="port",
                repo="acme/web",
                ref=None,
                file="site.yml",
                line=3,
                key_path=[0, "ansible.builtin.set_fact", "port"],
                how="sets",
                dependent=Dependent(repo="acme/web", ref=None, path=["acme/web", "acme/role"]),
            )
        )
    if "untaped_jira" in _SHIPPED:
        from untaped_jira.records import (
            Board,
            Comment,
            Issue,
            IssueLink,
            IssueOutcome,
            Project,
            Sprint,
            Transition,
            User,
        )

        rows += [
            User(name="jdoe", display_name="Jane"),
            Issue(
                key="ABC-1",
                updated_at="2026-06-01T13:00:00Z",
                labels=["a"],
                links=[IssueLink(key="ABC-2", direction="outward")],
                comments=[Comment(id="100", issue_key="ABC-1")],
            ),
            Comment(id="100", issue_key="ABC-1", created_at="2026-06-01T13:00:00Z"),
            IssueOutcome(action="transitioned", key="ABC-1", transition_id="11"),
            Transition(id="11", name="Start", to_status="Done"),
            Project(key="ABC", project_type_key="software"),
            Board(id=3, name="B"),
            Sprint(id=5, start_at="2026-06-01T13:00:00Z", origin_board_id=3),
        ]
    if "untaped_dotfiles" in _SHIPPED:
        from untaped_dotfiles.records import (
            ApplyOutcome,
            ItemOutcome,
            ItemRow,
            RemoveOutcome,
            RepoOutcome,
            RepoRow,
            StatusRow,
            StatusSummary,
            SyncOutcome,
        )

        placed = {
            "repo": "dotfiles",
            "item": "fish",
            "source": "fish/config.fish",
            "mode": "link",
            "action": "failed",
            "state": "conflict",
            "target_path": Path("/h/.config/fish/config.fish"),
            "error": Failure.from_exception(ConfigError("x")),
        }
        rows += [
            ItemRow(repo="dotfiles", name="fish", suggested="manual", files=2, skip=("conf",)),
            ItemOutcome(repo="dotfiles", name="fish", action="created", policy="sync"),
            RepoRow(
                name="dotfiles",
                url="https://example.com/acme/dotfiles.git",
                ref="main",
                managed=True,
                head="0" * 40,
                behind=1,
                target_path=Path("/h/repos/dotfiles"),
            ),
            RepoOutcome(name="dotfiles", action="deleted", detail="kept"),
            StatusRow(
                repo="dotfiles",
                item="fish",
                file="fish/config.fish",
                source="fish/config.fish",
                mode="link",
                policy="manual",
                state="applied",
                target_path=Path("/h/.config/fish/config.fish"),
            ),
            StatusSummary(checked_at=datetime(2026, 10, 2, tzinfo=UTC), total=1, attention=0),
            ApplyOutcome(**placed),
            SyncOutcome(**placed),
            RemoveOutcome(**placed),
        ]
    if "untaped_lint" in _SHIPPED:
        from untaped_lint.records import (
            FileLocation,
            Finding,
            Rule,
            ScaffoldOutcome,
            Subject,
            Tree,
        )

        rows += [
            Rule(id="no-print", reads="untaped.plugin", severity="error", summary="No print."),
            Finding(
                rule="lint.print",
                severity="error",
                target="acme",
                message="print()",
                location=FileLocation(path="src/a.py", line=3),
                error=Failure.from_exception(ConfigError("x")),
            ),
            Subject(kind="untaped.plugin", record={"name": "acme", "tags": [1, None]}),
            Tree(root="/repo", paths=("README.md", "src/a.py")),
            ScaffoldOutcome(action="created", target_path="/repo/.untaped/lint/no_print.py"),
        ]
    if "untaped_awx" in _SHIPPED:
        rows += _awx_samples()
    return {str(kind_of(type(row))): row for row in rows}


def _awx_samples() -> list[WireModel]:
    from untaped.sdk import ErrorCategory
    from untaped_awx.domain.case_failure import failure
    from untaped_awx.domain.outcomes import (
        CopyOutcome,
        JobCancelOutcome,
        JobRelaunchOutcome,
        RenameOutcome,
    )
    from untaped_awx.domain.ping import PingStatus
    from untaped_awx.domain.records import (
        ApplyOutcomeRow,
        DeleteOutcome,
        Document,
        JobEventRow,
        JobLogLine,
        JobRecord,
        LaunchOutcome,
        MembershipOutcome,
        ProvisionOutcome,
        PruneOutcome,
        SuiteCase,
        SyncOutcome,
        UnifiedTemplate,
    )
    from untaped_awx.domain.suite import CaseResult, ExpectationResult
    from untaped_awx.domain.workflow_node import WorkflowNode
    from untaped_awx.domain.workflow_usage import WorkflowUsage
    from untaped_awx.infrastructure.specs import ALL_SPECS

    answer = {"id": 1, "name": "deploy", "related": {"jobs": "/1/jobs/"}, "summary": None}
    started = {
        "id": 2,
        "kind": "job",
        "target_id": 1,
        "target_kind": "JobTemplate",
        "target_name": "deploy",
        "scope": {"organization": "Default"},
        "action": "completed",
        "status": "successful",
        "failed": False,
        "started_at": "2026-10-10T00:00:00Z",
    }
    copy = {
        "name": "deploy-tmp",
        "kind": "JobTemplate",
        "template": "deploy",
        "run_id": "r1",
        "ref": "main",
        "sha": "0" * 40,
        "created_at": "2026-10-10T00:00:00Z",
        "action": "planned",
    }
    return [
        *(spec.require_record().model_validate(answer) for spec in ALL_SPECS),
        JobRecord.model_validate({"id": 2, "kind": "job", "status": "new", "elapsed": 1.5}),
        UnifiedTemplate.model_validate({**answer, "type": "job_template"}),
        JobEventRow(
            job=2,
            counter=1,
            event="runner_on_ok",
            task="install",
            host=3,
            host_name="web-01",
            role=None,
            play="site",
            changed=True,
            failed=False,
            created=None,
            stdout="ok",
        ),
        JobLogLine(job=2, line="ok"),
        LaunchOutcome.model_validate(started),
        SyncOutcome.model_validate({**started, "kind": "project_update", "payload": {"a": [1]}}),
        ApplyOutcomeRow(
            id=1,
            kind="Project",
            scope={},
            identity={"name": "playbooks"},
            partial=False,
            unverified=False,
            name="playbooks",
            action="updated",
            fields_changed=["description"],
            preserved_secrets=[],
            dropped_undeclared_secrets=[],
            dropped_secrets=[],
            detail="",
        ),
        DeleteOutcome(id=1, kind="Project", name="playbooks", scope={}, action="deleted"),
        MembershipOutcome(
            id=1,
            kind="Inventory",
            name="prod",
            scope={},
            action="updated",
            field="hosts",
            associate=[2],
            disassociate=[],
        ),
        Document.model_validate(
            {
                "kind": "Project",
                "apiVersion": "untaped.awx/v1",
                "metadata": {"name": "playbooks", "organization": "Default"},
                "spec": {"scm_type": "git"},
            }
        ),
        SuiteCase(
            suite="smoke",
            case="ok",
            job_template="deploy",
            workflow_template=None,
            organization="Default",
            path="tests/smoke.yml",
            variables={"port": {"default": 80}},
        ),
        ProvisionOutcome.model_validate({**copy, "prompts": ["inventory"]}),
        PruneOutcome.model_validate({**copy, "id": 9, "action": "deleted"}),
        PingStatus(version="24.0.0", active_node="awx-1"),
        WorkflowNode(id=5, workflow_job_template=4, unified_job_template=1, name="deploy"),
        WorkflowUsage(id=4, name="pipeline", node_count=2),
        JobCancelOutcome(id=2, kind="job", action="canceled"),
        JobRelaunchOutcome(id=3, kind="job", target_id=2, action="completed"),
        CopyOutcome(name="deploy-2", source_id=1, kind="JobTemplate", action="planned"),
        RenameOutcome(id=1, name="deploy-2", old_name="deploy", kind="JobTemplate", action="done"),
        CaseResult(
            suite="smoke",
            case="ok",
            result="fail",
            job_id=2,
            started_at="2026-10-10T00:00:00Z",
            failure=failure("awx.playbook", ErrorCategory.INVALID, "a task failed"),
            expectations=(
                ExpectationResult(
                    check="status", expected="successful", actual="failed", passed=False
                ),
            ),
        ),
    ]


def test_every_kind_core_and_plugins_declare_round_trips_in_json_mode() -> None:
    _import_every_shipped_module()
    shipped = {kind for kind, model in record_kinds().items() if _is_shipped(model.__module__)}
    samples = _samples()

    assert set(samples) == shipped, "give every kind a package declares a sample here"
    for sample in samples.values():
        _assert_round_trips(sample)


#: The keys, in order, of the dict rows core's management commands wrote
#: before each declared a model: a model keeps its row's wire order.
_ROW_KEYS = {
    "untaped.auth_outcome": ["section", "profile", "store", "action", "detail", "error"],
    "untaped.token_source": ["profile", "section", "source"],
    "untaped.config_migration_outcome": ["profile", "from", "to", "action", "detail"],
    "untaped.doctor_check": ["check", "plugin", "status", "title", "detail", "fix", "automatic"],
    "untaped.fix_outcome": ["fix", "checks", "action", "detail"],
    "untaped.migration_row": ["id", "action", "source", "destination", "detail"],
    "untaped.setup_step": ["step", "plugin", "state", "detail", "run", "by"],
    "untaped.skill": ["name", "description", "source"],
    "untaped.installed_skill": ["name", "target", "scope", "state", "target_path"],
    "untaped.skill_outcome": ["name", "target", "scope", "target_path", "action"],
}


@pytest.mark.parametrize("kind", sorted(_ROW_KEYS))
def test_a_core_model_writes_its_old_row_keys_in_their_order(kind: str) -> None:
    sample = _samples()[kind]

    assert list(json.loads(sample.model_dump_json())) == _ROW_KEYS[kind]


class _Answered(Record, kind="acme-tools.answered"):
    """A service's row passed through: two typed fields, every other one kept."""

    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)

    id: int
    name: str
    organization: int | str | None = None


def test_an_open_record_dumps_the_row_it_was_given_typed_fields_first() -> None:
    row = {"type": "thing", "id": 7, "related": {"a": [1]}, "name": "n", "url": None}

    answered = _Answered.model_validate(row)

    assert answered.organization is None
    assert answered.model_dump(mode="json") == row
    assert list(answered.model_dump(mode="json")) == ["id", "name", "type", "related", "url"]
    _assert_round_trips(answered)


def test_an_open_record_writes_a_field_set_later_and_one_built_in_python() -> None:
    answered = _Answered.model_validate({"name": "n", "id": 1})

    assert answered.model_copy(update={"organization": "Default"}).model_dump() == {
        "id": 1,
        "name": "n",
        "organization": "Default",
    }
    assert _Answered(id=2, name="m", extra=[1]).model_dump() == {"id": 2, "name": "m", "extra": [1]}
    assert _Answered(id=2, name="m", organization=None).model_dump() == {
        "id": 2,
        "name": "m",
        "organization": None,
    }


def test_an_open_record_keeps_only_json_values() -> None:
    with pytest.raises(ValidationError):
        _Answered.model_validate({"id": 1, "name": "n", "when": object()})


@pytest.mark.parametrize("extras", [None, dict[str, Any]])
def test_an_open_record_without_json_typed_extras_fails_at_definition(extras: object) -> None:
    annotations: dict[str, object] = {"id": int}
    namespace: dict[str, object] = {"model_config": ConfigDict(extra="allow")}
    if extras is not None:
        annotations["__pydantic_extra__"] = extras
        namespace["__pydantic_extra__"] = Field(init=False)
    namespace["__annotations__"] = annotations
    with pytest.raises(TypeError, match=r'_Open: extra="allow" keeps fields no type checks'):
        type("_Open", (Record,), namespace)
