"""Behavioural tests for the shared record bases in ``untaped.records``."""

from __future__ import annotations

import importlib
import json
import pkgutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    Secret,
    SecretStr,
    ValidationError,
    computed_field,
    field_serializer,
    model_serializer,
)

from untaped.diagnostics import diagnostics_scope, failure_exit_code, note_failure
from untaped.errors import ConfigError, ErrorCategory, HttpStatusError, HttpTransportError
from untaped.records import (
    CheckRecord,
    DuplicateKindError,
    ErrorInfo,
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
    bare = _Bare(action="failed", error=ErrorInfo.from_exception(ConfigError("x")))
    assert list(bare.model_dump(mode="json")) == ["action", "error"]
    named = _Named(id=1, name="n", action="created")
    assert list(named.model_dump(mode="json")) == ["id", "name", "action", "detail"]


def test_redeclared_base_fields_keep_the_subclass_position(tmp_path: Path) -> None:
    row = _BranchOutcome(repo="a/b", action="updated", branch="main", target_path=tmp_path)

    assert list(row.model_dump(mode="json")) == ["repo", "action", "branch", "target_path"]


def test_a_failed_row_carries_the_error_of_the_exception_that_failed_it(tmp_path: Path) -> None:
    cause = HttpStatusError("HTTP 503", status_code=503, url="https://h/x", system="awx")

    row = _CloneOutcome(
        action="failed", target_path=tmp_path, repo="a/b", error=ErrorInfo.from_exception(cause)
    )

    assert row.model_dump(mode="json")["error"] == {
        "category": "unavailable",
        "system": "awx",
        "retryable": True,
        "message": "HTTP 503 for https://h/x",
        "hint": None,
    }


def test_rows_without_an_error_omit_the_field(tmp_path: Path) -> None:
    row = _CloneOutcome(action="cloned", target_path=tmp_path, repo="a/b")

    assert "error" not in row.model_dump(mode="json")
    assert "error" not in row.model_dump()


def test_error_info_splits_the_hint_off_an_overridden_message() -> None:
    cause = ConfigError("rejected\nhint: run `untaped config set awx.token --prompt`")

    info = ErrorInfo.from_exception(cause, message="token <redacted> rejected")

    assert (info.category, info.system, info.retryable) == ("config", "local", False)
    assert info.message == "token <redacted> rejected"
    info = ErrorInfo.from_exception(cause)
    assert info.message == "rejected"
    assert info.hint == "run `untaped config set awx.token --prompt`"


def test_error_info_of_an_unexpected_exception_is_a_failed_untaped_error() -> None:
    info = ErrorInfo.from_exception(KeyError("id"))

    assert (info.category, info.system, info.message) == ("failed", "untaped", "'id'")


def test_error_info_is_pure_and_note_failure_counts_it() -> None:
    with diagnostics_scope():
        ErrorInfo.from_exception(HttpTransportError("down", system="awx"))
        assert failure_exit_code() == 1

        info = note_failure(HttpTransportError("down", system="awx"), message="redacted")

        assert (info.category, info.system, info.message) == ("unavailable", "awx", "redacted")
        assert failure_exit_code() == 5


def test_note_failure_counts_an_error_info_or_a_category() -> None:
    info = ErrorInfo.from_exception(ConfigError("rejected", category="auth"))
    with diagnostics_scope():
        assert note_failure(info) is info
        assert failure_exit_code() == 4
    with diagnostics_scope():
        assert note_failure(ErrorCategory.UNAVAILABLE) is None
        assert failure_exit_code() == 5


def test_an_interrupt_is_interrupted_everywhere() -> None:
    info = ErrorInfo.from_exception(KeyboardInterrupt())
    with diagnostics_scope():
        note_failure(KeyboardInterrupt())
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
    error: ErrorInfo | None = None
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
            error=ErrorInfo.from_exception(ConfigError("x")),
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


def _samples() -> dict[str, Record]:
    from untaped.config.models import SettingOutcome, SettingRow
    from untaped.management.alias import AliasOutcome, AliasRow
    from untaped.management.plugin_check import PluginCheckRow
    from untaped.management.plugin_new import ScaffoldOutcome
    from untaped.management.plugins import ContractRow
    from untaped.profile.models import ProfileOutcome, ProfileRow

    rows: list[Record] = [
        SettingRow(key="k", value={"a": [1]}, default=None, source="default", profile=None),
        SettingOutcome(key="k", profile="default", action="updated"),
        AliasRow(name="ls", command="plugin list", argv=["plugin", "list"], profile="default"),
        AliasOutcome(
            name="ls",
            profile="default",
            action="failed",
            error=ErrorInfo.from_exception(ConfigError("x")),
        ),
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
    ]
    return {str(kind_of(type(row))): row for row in rows}


def test_every_kind_core_and_plugins_declare_round_trips_in_json_mode() -> None:
    _import_every_shipped_module()
    shipped = {kind for kind, model in record_kinds().items() if _is_shipped(model.__module__)}
    samples = _samples()

    assert set(samples) == shipped, "give every kind a package declares a sample here"
    for sample in samples.values():
        _assert_round_trips(sample)
