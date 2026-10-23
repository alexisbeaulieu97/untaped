"""Internal plugin composition kernel.

Implements the plugin pipeline: discovery and metadata pre-checks, entry-point
resolution, declaration validation, quarantine of every claimant of a contested
name, then app-factory staging and commit. Every plugin,
first-party ones included, arrives as an entry-point candidate; every violation
yields a :class:`QuarantineRecord` while composition continues. Doctor-check
bodies never run here.

This module is intentionally NOT re-exported: plugin authors import the
stable surface from :mod:`untaped.sdk` instead.
"""

from __future__ import annotations

import copy
import functools
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from importlib import import_module
from importlib import metadata as importlib_metadata
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, TypeGuard, cast

from cyclopts import App
from packaging.markers import UndefinedComparison, UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion
from pydantic import BaseModel, ConfigDict, Field, SecretStr, create_model, model_validator
from pydantic.fields import FieldInfo

from untaped.runtime.settings import (
    DEFAULT_CONFIG_PATH,
    PLUGIN_NAME_PATTERN,
    RESERVED_SECTION_KEYS,
    RESERVED_SECTIONS,
    legacy_credential_fields,
    reserved_section_keys,
    section_model,
)
from untaped.runtime.sink import Diagnostics
from untaped.sdk._block import OwnedBlock
from untaped.sdk._schema import unwrap_optional
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.deprecated_keys import key_mappings, mapping_errors
from untaped.sdk.health.marks import setup_mark_errors
from untaped.sdk.marks import (
    Deprecated,
    DeprecatedBehaviour,
    Stability,
    check_stability,
    deprecated,
    deprecated_parameters,
    mark_errors,
)
from untaped.sdk.network.credentials import (
    Credential,
    CredentialKey,
    credential_list,
    key_errors,
    key_fields,
)
from untaped.sdk.records import KindError, OutcomeRecord
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.state import (
    ProjectStateRows,
    StateCollection,
    StateMap,
    StateRows,
    caller_home,
    declared_rows,
)
from untaped.sdk.storage.store import Store

if TYPE_CHECKING:
    from untaped.sdk.contracts import Contract
    from untaped.sdk.storage.holds import ItemStore

#: What a plugin lists on ``PluginSpec.stores`` for the data it keeps in ``state.yml``.
type StateDeclaration = StateRows[Any] | ProjectStateRows[Any] | StateMap | StateCollection

#: Distribution whose version ``Requires-Dist: untaped`` is checked against.
_CORE_DISTRIBUTION = "untaped"

#: Entry-point group every plugin is discovered from.
PLUGINS_ENTRY_POINT_GROUP = "untaped.plugins"

#: Names no plugin may take, whatever they would collide with.
RESERVED_PLUGIN_NAMES = frozenset(
    {
        "untaped",
        "core",
        "sdk",
        "contracts",
        "plugins",
        "extensions",
        "caches",
        "profiles",
        "default",
        "shell",
        "state",
    }
)

#: Management commands the root app mounts beside the plugins, in mount
#: order. ``bootstrap`` mounts exactly these.
ROOT_MANAGEMENT_COMMANDS = (
    "agents",
    "config",
    "profile",
    "project",
    "skills",
    "doctor",
    "setup",
    "auth",
    "plugin",
)

#: Root commands core mounts; a plugin's CLI group is its name, so no plugin
#: may be named after one.
RESERVED_COMMAND_GROUPS = frozenset(ROOT_MANAGEMENT_COMMANDS)


@dataclass(frozen=True)
class SkillAsset:
    """A packaged agent skill shipped by a plugin.

    Without ``source`` it is a stub skill: core writes it from its own
    template and ``description``, and the stub sends the agent to
    ``untaped agents``. With ``source`` (deprecated) it is a copy of that
    directory. A plugin ships at most one stub.
    """

    name: str
    source: Path | None = None
    description: str = ""

    # Written by hand: a dataclass's generated ``__init__`` can't carry
    # ``@deprecated_parameters``, which is what warns about ``source``.
    @deprecated_parameters
    def __init__(
        self,
        name: str,
        source: Annotated[
            Path | None,
            deprecated(
                removed_in=12,
                replacement="a stub skill: leave source out",
            ),
        ] = None,
        description: str = "",
    ) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "description", description)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigError("skill asset name must not be empty")
        if not self.description.strip():
            raise ConfigError(f"skill asset {self.name!r} must have a description")


class DoctorCheck(OwnedBlock):
    """A health check contributed by the shell or a plugin.

    A plugin class holding it names it: ``store = DoctorCheck(title=..., run=...)``
    is the check ``<plugin>.store``; the 11.x spelling passes its ``id``. An
    ``online`` check contacts a remote service, so only ``untaped doctor
    --online`` runs it; every other check stays offline.
    """

    block_kind = "doctor-check"
    name_style = "kebab"

    title: str
    run: Callable[[PluginContext], DoctorResult]
    online: bool

    def __init__(
        self,
        id: str | None = None,
        *,
        title: str,
        run: Callable[[PluginContext], DoctorResult],
        online: bool = False,
    ) -> None:
        super().__init__(*_spelled(id))
        self.title = title
        self.run = run
        self.online = online

    @property
    def id(self) -> str:
        """``<plugin>.<name>``, or the id it was given."""
        return _id_of(self)

    def __repr__(self) -> str:
        return f"DoctorCheck({self._where()!r}, title={self.title!r})"


@dataclass(frozen=True)
class DoctorResult:
    """Outcome of one doctor-check body.

    ``ok=False`` is a failed row (doctor exits 1). ``ok=True`` with
    ``warn=True`` is a ``warn`` row: worth attention (a deprecated setting,
    say) but not a failure, so doctor still exits 0. ``fix`` is the
    ``untaped`` command (without the program name) that repairs a failed or
    warned row, as a string (split like a shell would) or an argv list; a
    value the user supplies is a ``<NAME>`` placeholder. Doctor emits it as
    the row's ``fix`` argv and shows it under the row.

    ``automatic=True`` says the fix is safe to run unattended: it needs no
    ``<NAME>`` value, no terminal input and no confirmation, it is a
    declared write (``@writes``) that takes ``--format``, and it carries any
    ``--yes`` it needs itself. It also asks for and prints no secret, so an
    agent may run it. Doctor fails the row when an automatic fix is missing
    or has a placeholder.
    """

    id: str
    ok: bool
    detail: str
    warn: bool = False
    fix: str | list[str] | None = None
    automatic: bool = False


@dataclass(frozen=True)
class PluginContext:
    """Frozen per-invocation snapshot handed to a doctor-check or migration body."""

    settings: BaseModel | None

    @property
    def diag(self) -> Diagnostics:
        """The stderr handle, as on ``AppContext``: resolved at each call."""
        return Diagnostics()


class MigrationOptions(BaseModel):
    """``untaped setup migrate-dirs``'s flags, passed to every migration.

    ``dissociate`` names one plugin's step (repacking clones that borrow
    objects from a directory, so it can go); every migration receives it.
    ``measure`` is false when only whether anything is left matters (doctor):
    a preview then leaves ``bytes`` at 0 instead of walking each directory.
    """

    model_config = ConfigDict(frozen=True)

    dry_run: bool = False
    dissociate: bool = False
    measure: bool = True


class MigrationRow(BaseModel):
    """One line of ``setup migrate-dirs``' preview: what a migration would do to one directory.

    ``action`` is ``move`` (``source`` → ``destination``), ``delete``,
    ``keep`` (left in place, ``detail`` says why) or ``then`` (what happens
    afterwards, such as a one-time download). ``source`` and ``destination``
    are absolute paths: core refuses to apply two migrations when one would
    delete what the other moves or keeps. ``bytes`` is the size of
    ``source`` (0 when not measured).
    """

    model_config = ConfigDict(frozen=True)

    action: Literal["move", "delete", "keep", "then"]
    source: str = ""
    destination: str = ""
    detail: str = ""
    bytes: int = 0


class MigrationOutcome(OutcomeRecord, kind="untaped.migration"):
    """What one migration did when applied: ``moved``, ``deleted``, ``unchanged``, ``failed``…"""

    table_columns: ClassVar[tuple[str, ...]] = ("id", "action", "detail")

    id: str
    detail: str = ""


def _spelled(id: str | None) -> tuple[str, str] | tuple[()]:
    """The plugin and name an 11.x id spells, or nothing for a block a plugin class names."""
    if id is None:
        return ()
    plugin, _, name = id.partition(".")
    return plugin, name


def _id_of(block: OwnedBlock) -> str:
    """A check's or migration's id: ``<plugin>.<name>``, or an 11.x id without a dot."""
    return block.plugin if not block.name else f"{block.plugin}.{block.name}"


class DirMigration(OwnedBlock):
    """A plugin's own directory moves for ``untaped setup migrate-dirs``.

    A plugin class holding it names it (``greetings = DirMigration(...)`` is
    ``<plugin>.greetings``); the 11.x spelling passes ``id``, which is
    ``<plugin>.<noun>``, unique across plugins. First consumer of the
    plugin class form: the 12 to 13 transition. ``preview``
    returns what would change and only reads; ``apply`` does it and returns
    its outcomes (usually one; core reports each).
    ``apply`` is idempotent: run again after a crash or a second time, it
    finishes what is left and changes nothing else. A migration is
    self-contained: it owns its old directory and everything pointing
    there. Core runs every plugin's migrations in registry order (by plugin
    name); one that relies on another plugin's having run first says so in
    its docstring.
    """

    block_kind = "dir-migration"
    name_style = "kebab"

    title: str
    preview: Callable[[PluginContext, MigrationOptions], Sequence[MigrationRow]]
    apply: Callable[[PluginContext, MigrationOptions], Sequence[MigrationOutcome]]

    def __init__(
        self,
        id: str | None = None,
        *,
        title: str,
        preview: Callable[[PluginContext, MigrationOptions], Sequence[MigrationRow]],
        apply: Callable[[PluginContext, MigrationOptions], Sequence[MigrationOutcome]],
    ) -> None:
        super().__init__(*_spelled(id))
        self.title = title
        self.preview = preview
        self.apply = apply

    @property
    def id(self) -> str:
        """``<plugin>.<name>``, or the id it was given."""
        return _id_of(self)

    def __repr__(self) -> str:
        return f"DirMigration({self._where()!r}, title={self.title!r})"


def _check_model(model: object, field: str, label: str) -> None:
    if model is not None and not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ConfigError(f"{label} {field} must be a pydantic BaseModel subclass or None")


def check_plugin_name(name: str) -> None:
    """Raise :class:`ConfigError` unless ``name`` is a valid plugin name.

    ``@`` is kept for a later ``<plugin>@<instance>`` form, so it gets its
    own message.
    """
    if "@" in name:
        raise ConfigError(f"plugin name {name!r} must not contain '@' (reserved)")
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        raise ConfigError(
            f"plugin name {name!r} must be lowercase words joined by single hyphens "
            f"(pattern {PLUGIN_NAME_PATTERN.pattern})"
        )


def _no_deprecations() -> Sequence[DeprecatedBehaviour]:
    return ()


@dataclass(frozen=True)
class ApplicationSpec:
    """The root application; ``deprecations`` lists core's deprecated behaviours."""

    name: str
    app_factory: Callable[[], App]
    section: str
    settings: type[BaseModel]
    state: type[BaseModel] | None = None
    skills: tuple[SkillAsset, ...] = ()
    doctor_checks: tuple[DoctorCheck, ...] = ()
    deprecations: Callable[[], Sequence[DeprecatedBehaviour]] = _no_deprecations

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.section.strip():
            raise ConfigError("shell name and section must not be empty")
        if not callable(self.deprecations):
            raise ConfigError("shell deprecations must be a function")
        _check_model(self.settings, "settings", "shell")
        _check_model(self.state, "state", "shell")
        key_mappings(self.settings)  # a broken shell declaration is a core bug
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "doctor_checks", tuple(self.doctor_checks))


def _no_contracts() -> Sequence[type[Contract]]:
    return ()


@dataclass(frozen=True)
class PluginSpec:
    """One composable plugin unit.

    ``name`` is also the plugin's config section and CLI group. Every part
    is optional: ``app_factory`` builds the commands mounted under the name
    (none when it is ``None``), ``settings`` is the settings model of the
    plugin's config section and ``state`` its ``state.yml`` model.

    ``help`` is the one-line summary shown in the Plugins panel of root help. A
    plugin that declares it is mounted lazily: its ``app_factory`` (and
    so its CLI import tree) runs only when the command is dispatched. Without
    it, the factory runs during composition and the listing falls back to
    the built app's own help.

    ``stability`` marks the whole plugin ``experimental`` or
    ``deprecated(...)``. It sits here, never on the app the factory returns:
    a lazy mount reads only the spec.

    ``contracts`` returns the :class:`~untaped.sdk.contracts.Contract` classes the
    plugin owns, and ``provides`` maps an owner plugin's name to a function
    returning this plugin's providers of that owner's contracts. Both are
    functions with a local import, so nothing under them is imported until a
    contract is first asked for (``untaped.sdk.contracts``).

    ``deprecations`` returns the plugin's deprecated behaviours
    (:func:`~untaped.sdk.marks.deprecated_behaviour`), a function with a local
    import for the same reason: each gets a ``behaviour`` line in the public
    surface, and its warnings name this plugin's distribution.

    ``caches`` are the :class:`~untaped.sdk.storage.cache.Cache` objects the plugin
    keeps; each gets the setting ``<name>.caches.<cache>.max_age_seconds``.
    ``stores`` is what the plugin keeps on disk outside its settings: the
    :class:`~untaped.sdk.storage.store.Store` objects, each file in a layout of its own
    (``doctor`` lists them), and the :class:`~untaped.sdk.storage.holds.ItemStore`
    objects whose items projects may hold (core asks the plugin how to
    release them), which share one namespace; and its ``state.yml`` data
    (:class:`~untaped.sdk.storage.state.StateRows`,
    :class:`~untaped.sdk.storage.state.ProjectStateRows`,
    :class:`~untaped.sdk.storage.state.StateMap`,
    :class:`~untaped.sdk.storage.state.StateCollection`), named in a namespace of their
    own: the keys of its state section. A ``StateRows`` the plugin declares
    must be listed. ``state`` (a model of the whole section) is still read
    alongside.

    ``credentials`` are the :class:`~untaped.sdk.network.credentials.Credential` objects
    whose secrets the plugin needs: core keeps them outside configuration,
    owns ``untaped auth login`` for them, and each still reads its 11.0
    ``<name>``/``<name>_command`` settings, which carry their own marks.

    ``guides`` is a folder of Markdown files (``files("untaped_acme") /
    "guides"``), each a page ``untaped agents <name> <guide>`` prints: prose
    that belongs to no single command. Composition never reads it.
    """

    name: str
    app_factory: Callable[[], App] | None = None
    settings: type[BaseModel] | None = None
    state: type[BaseModel] | None = None
    skills: tuple[SkillAsset, ...] = ()
    doctor_checks: tuple[DoctorCheck, ...] = ()
    help: str | None = None
    stability: Stability | None = None
    contracts: Callable[[], Sequence[type[Contract]]] = _no_contracts
    provides: Mapping[str, Callable[[], Sequence[Contract]]] = field(
        default_factory=lambda: MappingProxyType({}), hash=False
    )
    migrations: tuple[DirMigration, ...] = ()
    caches: tuple[Cache[Any], ...] = ()
    stores: tuple[Store | ItemStore[Any] | StateDeclaration, ...] = ()
    credentials: tuple[Credential[Any], ...] = ()
    deprecations: Callable[[], Sequence[DeprecatedBehaviour]] = _no_deprecations
    guides: Traversable | None = field(default=None, hash=False)
    _home: str = field(default="", init=False, repr=False, compare=False, hash=False)
    """The module that made the spec (``pkg.`` for a package): its own declarations live there."""

    def __post_init__(self) -> None:
        check_plugin_name(self.name)
        object.__setattr__(self, "_home", caller_home(frozenset({__name__, "dataclasses"})))
        _check_model(self.settings, "settings", f"plugin {self.name!r}")
        _check_model(self.state, "state", f"plugin {self.name!r}")
        if self.app_factory is not None and not callable(self.app_factory):
            raise ConfigError(f"plugin {self.name!r} app_factory must be callable or None")
        try:
            check_stability(self.stability, where=f"plugin {self.name!r}")
        except TypeError as exc:
            raise ConfigError(str(exc)) from exc
        if self.help is not None and (
            not isinstance(self.help, str) or not self.help.strip() or "\n" in self.help
        ):
            raise ConfigError(f"plugin {self.name!r} help must be a non-empty single line or None")
        if self.help is not None and self.app_factory is None:
            raise ConfigError(
                f"plugin {self.name!r} help describes commands it has no app_factory for"
            )
        _check_listings(self)
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "doctor_checks", tuple(self.doctor_checks))
        object.__setattr__(self, "migrations", tuple(self.migrations))
        object.__setattr__(self, "caches", tuple(self.caches))
        for cache in self.caches:
            if not isinstance(cache, Cache):
                raise ConfigError(f"plugin {self.name!r} caches holds {cache!r}, not a Cache")
        try:
            object.__setattr__(self, "credentials", credential_list(self.credentials))
        except TypeError as exc:
            raise ConfigError(f"plugin {self.name!r} {exc}") from None
        object.__setattr__(self, "stores", tuple(self.stores))
        for store in self.stores:
            if _is_state(store):
                _check_state_listing(self.name, store)
            elif not isinstance(store, Store) and not _is_item_store(store):
                raise ConfigError(
                    f"plugin {self.name!r} stores holds {store!r}, not a Store, an ItemStore "
                    "or a state declaration"
                )
        if self.guides is not None and not isinstance(self.guides, Traversable):
            raise ConfigError(
                f"plugin {self.name!r} guides is {self.guides!r}, not a folder "
                "(a Traversable, such as files(package) / 'guides')"
            )
        object.__setattr__(self, "provides", MappingProxyType(dict(self.provides)))


def _check_listings(spec: PluginSpec) -> None:
    """``contracts``, ``deprecations`` and each ``provides`` entry are functions."""
    for listing in ("contracts", "deprecations"):
        if not callable(getattr(spec, listing)):
            raise ConfigError(f"plugin {spec.name!r} {listing} must be a function")
    for owner, providers in spec.provides.items():
        check_plugin_name(owner)
        if not callable(providers):
            raise ConfigError(f"plugin {spec.name!r} provides[{owner!r}] must be a function")


class _NoSettings(BaseModel):
    """The settings of a plugin that declares none: no fields."""


def owns_contracts(spec: PluginSpec) -> bool:
    """Whether ``spec`` declares contracts (read from the spec; nothing is imported)."""
    return spec.contracts is not _no_contracts


def contract_owners(result: CompositionResult) -> tuple[str, ...]:
    """The composed plugins that own contracts, which a ranking may name."""
    return tuple(each.spec.name for each in result.plugins if owns_contracts(each.spec))


def section_settings(spec: PluginSpec, *, extensions: bool = True) -> type[BaseModel] | None:
    """The model registered for ``spec``'s config section; ``None`` when it has none.

    The section of a contract owner, or of a plugin that declares caches,
    always exists: it carries the keys the SDK injects, ``extensions`` and
    ``caches`` (:func:`untaped.runtime.settings.section_model`). So does the
    section of a plugin declaring credentials: it carries their deprecated
    11.0 settings. ``extensions=False`` leaves ``extensions`` out (the config
    reference documents it elsewhere).
    """
    owner = extensions and owns_contracts(spec)
    if owner or spec.caches or spec.credentials:
        return section_model(
            spec.settings or _NoSettings,
            extensions=owner,
            caches=spec.caches,
            section=spec.name,
            credentials=spec.credentials,
        )
    return spec.settings


def settings_model(spec: PluginSpec) -> type[BaseModel]:
    """The plugin's section model, or a model with no fields when it has none."""
    return section_settings(spec) or _NoSettings


def plugin_dir(spec: PluginSpec) -> Path:
    """The directory a plugin keeps its own data in: ``~/.untaped/plugins/<name>/``.

    It takes the spec rather than reading any ambient state, so it works
    from any thread. The directory is not created.
    """
    return named_plugin_dir(spec.name)


def named_plugin_dir(name: str) -> Path:
    """:func:`plugin_dir` by the plugin's name, for core code that holds a name, not a spec."""
    return plugins_root() / name


def plugins_root() -> Path:
    """``~/.untaped/plugins/``: every plugin's folder, installed here or not."""
    return Path(DEFAULT_CONFIG_PATH).expanduser().parent / "plugins"


@dataclass(frozen=True)
class PluginRef:
    """How a composed plugin arrived.

    ``distribution_version`` and ``requires_dist`` are the distribution's
    metadata (contract providers check their owner's range against it);
    they don't take part in equality.
    """

    distribution: str
    entry_point: str
    distribution_version: str = field(default="", compare=False)
    requires_dist: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class RegisteredPlugin:
    """A fully validated, committed plugin."""

    spec: PluginSpec
    plugin_ref: PluginRef
    skills: tuple[SkillAsset, ...]
    #: App staged by the one validating ``app_factory`` call, reused at
    #: mount time; ``None`` when the spec sets ``help`` (deferred) or has
    #: no ``app_factory``.
    app: App | None = None


#: Every valid quarantine/diagnostic reason code lives here.
VALID_REASONS = frozenset(
    {
        "reserved-name",
        "duplicate-name",
        "settings-state-overlap",
        "duplicate-skill",
        "bad-skill-asset",
        "duplicate-doctor-check",
        "doctor-check-failed",
        "duplicate-migration",
        "bad-migration",
        "malformed-entry-point",
        "not-a-spec",
        "bad-app-factory",
        "bad-metadata",
        "bad-settings-keys",
        "duplicate-kind",
        "foreign-kind",
        "foreign-cache",
        "duplicate-cache",
        "foreign-store",
        "duplicate-store",
        "unlisted-state",
        "foreign-credential",
        "duplicate-credential",
        "bad-credential",
    }
)


@dataclass(frozen=True)
class QuarantineRecord:
    """Why a plugin was excluded.

    ``name`` is the candidate's entry-point (plugin) name.
    """

    name: str
    distribution: str
    entry_point: str
    reason: str
    detail: str

    def __post_init__(self) -> None:
        if self.reason not in VALID_REASONS:
            raise ConfigError(f"unknown quarantine reason: {self.reason!r}")
        if not self.detail.strip():
            raise ConfigError("quarantine detail must not be empty")


@dataclass(frozen=True)
class PluginCandidate:
    """One discovered candidate awaiting composition.

    ``target`` is the entry point's ``module:attr`` string, or the
    :class:`PluginSpec` itself for a plugin composed without installing it.
    ``distribution_version``, ``entry_point_group``, and ``requires_dist``
    are captured at discovery via :mod:`importlib.metadata` without importing
    plugin code; ``untaped plugin list`` reports ``distribution_version``
    for candidates.
    """

    distribution: str
    name: str
    target: object
    distribution_version: str = ""
    entry_point_group: str = PLUGINS_ENTRY_POINT_GROUP
    requires_dist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires_dist", tuple(self.requires_dist))


@dataclass(frozen=True)
class CompositionResult:
    """Committed plugins plus quarantine records for one composition.

    ``candidates`` are the ones it was composed from, so a reader of the
    composition (``untaped plugin list``'s records) reads the same ones.
    """

    plugins: tuple[RegisteredPlugin, ...] = ()
    quarantine: tuple[QuarantineRecord, ...] = ()
    candidates: tuple[PluginCandidate, ...] = field(default=(), compare=False)


def candidate_distribution(candidate: PluginCandidate) -> str:
    """The distribution a candidate is reported under; a blank one is ``unknown``."""
    return candidate.distribution.strip() or "unknown"


class _Quarantine(Exception):
    """Internal control flow: one plugin failed validation.

    :func:`compose` converts it to a :class:`QuarantineRecord`.
    """

    def __init__(self, reason: str, detail: str, entry_point: str | None = None) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.entry_point = entry_point

    def to_record(self, candidate: PluginCandidate) -> QuarantineRecord:
        distribution = candidate_distribution(candidate)
        entry_point = (
            self.entry_point if self.entry_point is not None else candidate_entry_point(candidate)
        )
        return QuarantineRecord(
            name=candidate.name,
            distribution=distribution,
            entry_point=entry_point,
            reason=self.reason,
            detail=self.detail,
        )


def parse_requirement(requirement: object) -> Requirement | None:
    """Parse one PEP 508 Requires-Dist string; ``None`` when malformed."""
    if not isinstance(requirement, str):
        return None
    try:
        return Requirement(requirement)
    except InvalidRequirement:
        return None


#: What ``requirement_admits`` raises on an entry whose marker or version it cannot evaluate.
UNEVALUABLE = (InvalidVersion, UndefinedComparison, UndefinedEnvironmentName, KeyError)


def requirement_admits(requirement: Requirement, sdk_version: str) -> bool:
    """Whether a parsed Requires-Dist entry admits ``sdk_version``.

    An entry whose environment marker does not apply (e.g. an ``extra``
    nobody requested) constrains nothing. A direct reference (PEP 508 URL)
    is a pinned source, not a version range, so admission cannot be
    disproved. Pre-releases of the running SDK are compared per PEP 440
    (``6.1.0.dev3`` does not satisfy ``>=6.1.0``).
    """
    if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
        return True
    if requirement.url:
        return True
    return requirement.specifier.contains(sdk_version, prereleases=True)


def owner_requirement(
    requires_dist: Sequence[str], owner: str, distribution: str | None = None
) -> Requirement | None:
    """The requirement on ``owner``'s distribution that the ``<owner>`` extra adds.

    A provider declares the plugin it fills contracts for under an extra named
    after it (``untaped-acme[workspace]``), so ``distribution``
    (``untaped-<owner>`` by default) is required only with that extra. The
    import lint, the ``provides-requirement`` convention and the
    ``owner-out-of-range`` quarantine all read this one requirement.
    """
    wanted = canonicalize_name(distribution or f"untaped-{owner}")
    for line in requires_dist:
        parsed = parse_requirement(line)
        if parsed is None or parsed.marker is None or canonicalize_name(parsed.name) != wanted:
            continue
        # The environment fills every other variable, so ``extra`` decides; a marker
        # naming one it can't (``extras``) adds no owner requirement.
        try:
            added = parsed.marker.evaluate({"extra": owner})
            added = added and not parsed.marker.evaluate({"extra": ""})
        except KeyError:
            continue
        if added:
            return parsed
    return None


def declared_distributions(
    requirements: Sequence[str], extras: frozenset[str] = frozenset()
) -> set[str]:
    """Canonical names of the distributions ``requirements`` (its ``Requires-Dist``) declare.

    That is what a default install pulls in, plus what an extra in
    ``extras`` (the owner keys the plugin fills) adds: the owner a provider
    declares under ``untaped-acme[<owner>]``. Other extra-guarded
    requirements are left out; other markers (``python_version``,
    ``sys_platform``…) are ignored; invalid strings are skipped.
    """
    names: set[str] = set()
    for requirement in requirements:
        parsed = parse_requirement(requirement)
        if parsed is None:
            continue
        if (
            parsed.marker is None
            or not mentions_extra(str(parsed.marker))
            or _added_by(parsed, extras)
        ):
            names.add(canonicalize_name(parsed.name))
    return names


def _added_by(requirement: Requirement, extras: frozenset[str]) -> bool:
    """Whether one of ``extras`` turns ``requirement`` on."""
    marker = requirement.marker
    if marker is None:
        return False
    try:
        return any(marker.evaluate({"extra": extra}) for extra in extras)
    except KeyError:  # a variable the environment can't fill (``extras``)
        return False


def mentions_extra(marker: str) -> bool:
    """Whether ``marker`` tests the ``extra`` variable (quoted values aside)."""
    return re.search(r"\bextra\b", re.sub(r"\"[^\"]*\"|'[^']*'", "", marker)) is not None


def admits(requirement: Requirement, version: str) -> bool:
    """Whether ``requirement``'s range admits ``version`` (a direct reference always does)."""
    if requirement.url:
        return True
    try:
        return requirement.specifier.contains(version, prereleases=True)
    except InvalidVersion:
        return True


def range_text(requirement: Requirement) -> str:
    """``requirement``'s range as people write it, lower bound first: ``>=2,<3``."""
    return ",".join(
        sorted((str(each) for each in requirement.specifier), key=lambda each: each[0] != ">")
    )


def _running_sdk_version() -> str | None:
    """Running SDK version via importlib.metadata; None when unresolvable."""
    try:
        return importlib_metadata.version(_CORE_DISTRIBUTION)
    except importlib_metadata.PackageNotFoundError:
        return None


def _check_entry_point_group(candidate: PluginCandidate) -> None:
    if candidate.entry_point_group != PLUGINS_ENTRY_POINT_GROUP:
        raise _Quarantine(
            "bad-metadata",
            f"entry-point group {candidate.entry_point_group!r} of distribution "
            f"{candidate.distribution!r} is not the plugins group "
            f"{PLUGINS_ENTRY_POINT_GROUP!r}",
        )


def _check_requires_dist(candidate: PluginCandidate, state: _CompositionState) -> None:
    untaped_requirements: list[tuple[str, Requirement]] = []
    for requirement in candidate.requires_dist:
        parsed = parse_requirement(requirement)
        if parsed is None:
            raise _Quarantine(
                "bad-metadata",
                f"malformed Requires-Dist entry {requirement!r} of distribution "
                f"{candidate.distribution!r}",
            )
        if canonicalize_name(parsed.name) == _CORE_DISTRIBUTION:
            untaped_requirements.append((str(requirement), parsed))
    if not untaped_requirements:
        return
    sdk_version = state.sdk_version
    if sdk_version is None:
        raise _Quarantine(
            "bad-metadata",
            f"could not resolve running SDK version to check Requires-Dist "
            f"of distribution {candidate.distribution!r}",
        )
    for requirement, parsed in untaped_requirements:
        try:
            admits = requirement_admits(parsed, sdk_version)
        except UNEVALUABLE:
            raise _Quarantine(
                "bad-metadata",
                f"malformed Requires-Dist entry {requirement!r} of distribution "
                f"{candidate.distribution!r}",
            ) from None
        if not admits:
            raise _Quarantine(
                "bad-metadata",
                f"Requires-Dist {requirement!r} of distribution "
                f"{candidate.distribution!r} does not admit running SDK "
                f"version {sdk_version}",
            )


def discover_candidates(*, group: str = PLUGINS_ENTRY_POINT_GROUP) -> tuple[PluginCandidate, ...]:
    """Discover every plugin candidate from entry points.

    Reads distribution version, entry-point group, and Requires-Dist strings
    via :mod:`importlib.metadata` without importing any plugin code.
    """
    found: list[PluginCandidate] = []
    # Entry points of one distribution share its object; read its metadata once.
    read: dict[int, tuple[str, str, tuple[str, ...]]] = {}
    for entry_point in importlib_metadata.entry_points(group=group):
        dist = entry_point.dist
        if dist is None:
            found.append(
                PluginCandidate(
                    distribution="unknown",
                    name=entry_point.name,
                    target=entry_point.value,
                    entry_point_group=entry_point.group,
                )
            )
            continue
        if id(dist) not in read:
            name = dist.metadata.get("Name") or "unknown"
            read[id(dist)] = (str(name), dist.version, tuple(dist.requires or ()))
        dist_name, dist_version, requires = read[id(dist)]
        found.append(
            PluginCandidate(
                distribution=dist_name,
                name=entry_point.name,
                target=entry_point.value,
                distribution_version=dist_version,
                entry_point_group=entry_point.group,
                requires_dist=requires,
            )
        )
    return tuple(found)


class _CompositionState:
    """Mutable accumulation of one composition run (shell + committed plugins)."""

    def __init__(self, shell: ApplicationSpec) -> None:
        self.shell = shell
        self.skill_names: set[str] = {skill.name for skill in shell.skills}
        self.doctor_ids: set[str] = {check.id for check in shell.doctor_checks}
        self.migration_ids: set[str] = set()

    @cached_property
    def sdk_version(self) -> str | None:
        """The running SDK version, resolved once per composition when first needed."""
        return _running_sdk_version()


def reserved_as(name: str) -> str | None:
    """What a reserved ``name`` would collide with; ``None`` when it is free."""
    if name in RESERVED_PLUGIN_NAMES:
        return "plugin name"
    if name in RESERVED_SECTIONS:
        return "config section"
    if name in RESERVED_COMMAND_GROUPS:
        return "command group"
    return None


def _check_reserved_name(spec: PluginSpec, state: _CompositionState) -> None:
    reserved = reserved_as(spec.name)
    if reserved is not None:
        raise _Quarantine("reserved-name", f"reserved {reserved}: {spec.name!r}")
    if spec.name in (state.shell.name, state.shell.section):
        raise _Quarantine(
            "duplicate-name",
            f"duplicate plugin name: {spec.name!r} (already provided by the shell)",
        )


def _check_section_keys(spec: PluginSpec) -> None:
    # Only an owner's settings section gets ``extensions``, and only one with caches ``caches``.
    injected = frozenset(
        ({"extensions"} if owns_contracts(spec) else set())
        | ({"caches"} if spec.caches else set())
        | set(
            legacy_credential_fields(
                spec.settings or _NoSettings, spec.credentials, section=spec.name
            )
        )
    )
    for label, model, keys in (
        ("settings", spec.settings, injected),
        ("state", spec.state, frozenset[str]()),
    ):
        taken = [] if model is None else reserved_section_keys(model, injected=keys)
        if label == "state":
            taken += [
                each.name
                for each in state_declarations(spec)
                if each.name in RESERVED_SECTION_KEYS and each.name not in taken
            ]
        if taken:
            raise _Quarantine(
                "bad-settings-keys",
                f"plugin {spec.name!r} {label} declares {', '.join(taken)}, "
                "which untaped reserves for its own section keys",
            )


def _check_state_model(spec: PluginSpec) -> None:
    if spec.state is not None:
        marked = mark_errors(spec.state, state=True)
        if marked:
            raise _Quarantine("bad-settings-keys", f"plugin {spec.name!r}: {marked[0]}")
    if spec.settings is None:
        return
    overlap = sorted(set(spec.settings.model_fields) & state_keys(spec))
    if overlap:
        raise _Quarantine(
            "settings-state-overlap",
            f"overlapping profile/state settings for section {spec.name!r}: {', '.join(overlap)}",
        )


def _check_skills(spec: PluginSpec, state: _CompositionState) -> None:
    # Shape before collision: a collision needs a valid name to report.
    seen_skills: set[str] = set()
    for index, skill in enumerate(spec.skills):
        if (
            not isinstance(skill, SkillAsset)
            or not skill.name.strip()
            or not skill.description.strip()
        ):
            raise _Quarantine(
                "bad-skill-asset",
                f"malformed skill asset at index {index} of plugin {spec.name!r}: {skill!r}",
            )
        if skill.name in state.skill_names or skill.name in seen_skills:
            raise _Quarantine("duplicate-skill", f"duplicate skill name: {skill.name!r}")
        seen_skills.add(skill.name)
    stubs = [skill.name for skill in spec.skills if skill.source is None]
    if len(stubs) > 1:
        raise _Quarantine(
            "bad-skill-asset",
            f"plugin {spec.name!r} ships more than one stub skill (no source): {', '.join(stubs)}",
        )


def _check_doctor_checks(spec: PluginSpec, state: _CompositionState) -> None:
    seen_checks: set[str] = set()
    for check in spec.doctor_checks:
        if (
            not isinstance(check, DoctorCheck)
            or not check.id.strip()
            or not check.title.strip()
            or not callable(check.run)
        ):
            raise _Quarantine(
                "doctor-check-failed",
                f"malformed doctor check of plugin {spec.name!r}: {check!r}",
            )
        if check.id in state.doctor_ids or check.id in seen_checks:
            raise _Quarantine("duplicate-doctor-check", f"duplicate doctor id: {check.id!r}")
        seen_checks.add(check.id)


def _check_migrations(spec: PluginSpec, state: _CompositionState) -> None:
    seen: set[str] = set()
    for migration in spec.migrations:
        if (
            not isinstance(migration, DirMigration)
            or not migration.title.strip()
            or not callable(migration.preview)
            or not callable(migration.apply)
        ):
            raise _Quarantine(
                "bad-migration", f"malformed migration of plugin {spec.name!r}: {migration!r}"
            )
        prefix, dot, noun = migration.id.partition(".")
        if prefix != spec.name or not dot or not noun.strip():
            raise _Quarantine(
                "bad-migration",
                f"migration id {migration.id!r} of plugin {spec.name!r} is not "
                f"'{spec.name}.<noun>'",
            )
        if migration.id in state.migration_ids or migration.id in seen:
            raise _Quarantine("duplicate-migration", f"duplicate migration id: {migration.id!r}")
        seen.add(migration.id)


def _check_setup_marks(spec: PluginSpec) -> None:
    # ``NeedsSetup`` and ``Secret`` take effect only where core can act on them.
    if spec.settings is None:
        return
    errors = setup_mark_errors(spec.settings)
    if errors:
        raise _Quarantine("bad-settings-keys", f"plugin {spec.name!r}: {errors[0]}")


def _check_key_mappings(spec: PluginSpec) -> None:
    # The plugin's own model: an old key may never point at an injected key.
    if spec.settings is None:
        return
    errors = mapping_errors(spec.settings)
    if errors:
        raise _Quarantine("bad-settings-keys", f"plugin {spec.name!r}: {errors[0]}")


def _check_caches(spec: PluginSpec) -> None:
    seen: set[str] = set()
    for cache in spec.caches:
        if cache.plugin != spec.name:
            raise _Quarantine(
                "foreign-cache",
                f"plugin {spec.name!r} declares the cache {cache.qualified!r} of another plugin",
            )
        if cache.name in seen:
            raise _Quarantine(
                "duplicate-cache", f"plugin {spec.name!r} declares the cache {cache.name!r} twice"
            )
        seen.add(cache.name)


def _check_credentials(spec: PluginSpec) -> None:
    seen: set[str] = set()
    fields = {} if spec.settings is None else spec.settings.model_fields
    holders: dict[type[CredentialKey], list[str]] = {}
    if spec.settings is not None:
        for path, model in key_fields(spec.settings):
            holders.setdefault(model, []).append(".".join(path))
    for credential in spec.credentials:
        if credential.plugin != spec.name:
            raise _Quarantine(
                "foreign-credential",
                f"plugin {spec.name!r} declares the credential {credential.qualified!r} of "
                "another plugin",
            )
        if credential.name in seen:
            raise _Quarantine(
                "duplicate-credential",
                f"plugin {spec.name!r} declares the credential {credential.name!r} twice",
            )
        seen.add(credential.name)
        errors = key_errors(credential.key)
        if errors:
            raise _Quarantine(
                "bad-credential",
                f"plugin {spec.name!r} credential {credential.name!r}: {errors[0]}",
            )
        clash = fields.get(credential.name)
        if clash is not None and not _kept_secret_field(clash):
            raise _Quarantine(
                "bad-credential",
                f"plugin {spec.name!r} credential {credential.name!r} has the name of a "
                "setting of its section; a credential is not a setting",
            )
        held = holders.get(credential.key, [])
        if len(held) > 1:
            raise _Quarantine(
                "bad-credential",
                f"plugin {spec.name!r} credential {credential.name!r}: several settings hold a "
                f"{credential.key.__name__} ({', '.join(held)}); keep one",
            )


def _kept_secret_field(field: FieldInfo) -> bool:
    """Whether ``field`` is an 11.0 secret setting kept, deprecated, beside its credential."""
    deprecated_mark = any(isinstance(item, Deprecated) for item in field.metadata)
    return deprecated_mark and unwrap_optional(field.annotation) is SecretStr


def _check_stores(spec: PluginSpec) -> None:
    files: set[str] = set()
    state: set[str] = set()
    for store in spec.stores:
        what = "state" if _is_state(store) else "store"
        if store.plugin != spec.name:
            raise _Quarantine(
                "foreign-store",
                f"plugin {spec.name!r} declares the {what} '{store.plugin}.{store.name}' of "
                "another plugin",
            )
        seen = state if _is_state(store) else files
        if store.name in seen:
            raise _Quarantine(
                "duplicate-store", f"plugin {spec.name!r} declares the {what} {store.name!r} twice"
            )
        seen.add(store.name)
    listed = state_declarations(spec)
    for declared in declared_rows(spec.name, spec._home):
        if declared not in listed:
            raise _Quarantine(
                "unlisted-state",
                f"plugin {spec.name!r} declares the state rows {declared.qualified!r} but "
                "doesn't list them on PluginSpec.stores",
            )


def state_declarations(spec: PluginSpec) -> tuple[StateDeclaration, ...]:
    """The ``state.yml`` data ``spec`` lists on ``stores``."""
    return tuple(store for store in spec.stores if _is_state(store))


def state_keys(spec: PluginSpec | ApplicationSpec) -> frozenset[str]:
    """The keys of ``spec``'s state section: its state declarations' and its ``state`` model's.

    Read from names only: no row model is imported.
    """
    keys = set() if spec.state is None else set(spec.state.model_fields)
    if isinstance(spec, PluginSpec):
        keys.update(declared.name for declared in state_declarations(spec))
    return frozenset(keys)


def state_model(spec: PluginSpec | ApplicationSpec) -> type[BaseModel] | None:
    """One model of ``spec``'s whole state section in the home; ``None`` when it keeps none.

    The ``state`` model's fields, then a field per state declaration it
    doesn't cover: rows of the declaration's model, a string map, or a list
    of mappings. A project's rows live in the project and are left out.
    Resolves lazy row models: for the surface, the config reference and
    ``doctor``, never on the startup path.
    """
    declarations = (
        tuple(each for each in state_declarations(spec) if not isinstance(each, ProjectStateRows))
        if isinstance(spec, PluginSpec)
        else ()
    )
    covered = set() if spec.state is None else set(spec.state.model_fields)
    extra: dict[str, Any] = {
        each.name: (_declared_type(each), Field(default_factory=_EMPTY[type(each)]))
        for each in declarations
        if each.name not in covered
    }
    if not extra:
        return spec.state
    fields: dict[str, Any] = {
        name: (info.annotation, info)
        for name, info in (spec.state.model_fields.items() if spec.state is not None else ())
    }
    model: type[BaseModel] = create_model(f"{_title(spec.name)}State", **fields, **extra)
    return model


def spliced_state(spec: PluginSpec) -> type[BaseModel] | None:
    """The ``state`` model as spliced into settings, less the keys a state declaration owns.

    Declared state keeps its own rules (unknown fields kept, a damaged row
    named), so it never fails the plugin's settings load.
    """
    if spec.state is None:
        return None
    owned = {each.name for each in state_declarations(spec)}
    if not owned:
        return spec.state
    return _without(spec.state, frozenset(owned))


@functools.cache
def _without(model: type[BaseModel], keys: frozenset[str]) -> type[BaseModel]:
    """A subclass of ``model`` that never reads ``keys``, one class per pair.

    A subclass keeps the model's validators and methods. The keys are dropped
    before validation, so their fields keep their defaults: registering the
    class again is a no-op.
    """
    forbids = model.model_config.get("extra") == "forbid"

    class Spliced(model):  # type: ignore[misc,valid-type]
        # The dropped keys stay in the file: a model forbidding extra keys would refuse them.
        if forbids:
            model_config = ConfigDict(extra="ignore")

        @model_validator(mode="before")
        @classmethod
        def _drop_declared(cls, data: object) -> object:
            if isinstance(data, dict):
                return {key: value for key, value in data.items() if key not in keys}
            return data

    # A declared field keeps its usual empty value; one with no default is None.
    fields: dict[str, Any] = {}
    for name, info in model.model_fields.items():
        if name not in keys:
            continue
        if info.is_required():
            fields[name] = (Any, Field(default=None, exclude=True))
        else:
            kept = copy.copy(info)
            kept.exclude = True
            fields[name] = (info.annotation, kept)
    without = create_model(model.__name__, __base__=Spliced, __module__=model.__module__, **fields)
    return cast("type[BaseModel]", without)


_EMPTY: dict[type, Callable[[], object]] = {
    StateRows: list,
    ProjectStateRows: list,
    StateMap: dict,
    StateCollection: list,
}


def _declared_type(declared: StateDeclaration) -> Any:
    if isinstance(declared, StateRows | ProjectStateRows):
        model = declared.model if isinstance(declared.model, type) else declared.model()
        return list[model]  # type: ignore[valid-type]
    if isinstance(declared, StateMap):
        return dict[str, str]
    return list[dict[str, Any]]


def _title(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("-"))


def _is_state(value: object) -> TypeGuard[StateDeclaration]:
    return isinstance(value, StateRows | ProjectStateRows | StateMap | StateCollection)


def _check_state_listing(plugin: str, declared: StateDeclaration) -> None:
    """A state declaration is listed as declared: never one project's rows or map."""
    bound = (declared._file._path if isinstance(declared, StateRows) else None) or (
        declared._path if isinstance(declared, StateMap) else None
    )
    if bound is not None:
        raise ConfigError(
            f"plugin {plugin!r} stores lists {declared.plugin}.{declared.name} of one project; "
            "list the "
            "declaration (StateRows.per_project, StateMap without project=)"
        )


def _check_composed_mappings(spec: PluginSpec) -> None:
    # A cache's renamed_from exists on the composed model only: check it there.
    if not any(cache.renamed_from for cache in spec.caches):
        return
    try:
        errors = mapping_errors(settings_model(spec))
    except ConfigError as exc:
        errors = [str(exc)]
    if errors:
        raise _Quarantine(
            "bad-settings-keys", f"plugin {spec.name!r} caches renamed_from: {errors[0]}"
        )


def _check_declaration(spec: PluginSpec, state: _CompositionState) -> None:
    _check_reserved_name(spec, state)
    _check_caches(spec)
    _check_credentials(spec)
    _check_stores(spec)
    _check_setup_marks(spec)
    _check_section_keys(spec)
    _check_key_mappings(spec)
    _check_composed_mappings(spec)
    _check_state_model(spec)
    _check_skills(spec, state)
    _check_doctor_checks(spec, state)
    _check_migrations(spec, state)


def _check_factory(spec: PluginSpec, factory: Callable[[], App]) -> App:
    try:
        staged = factory()
    except KindError as exc:
        raise _Quarantine(exc.reason, str(exc)) from None
    except Exception as exc:
        raise _Quarantine(
            "bad-app-factory",
            f"app factory of plugin {spec.name!r} raised: {exc}",
        ) from None
    if not isinstance(staged, App):
        raise _Quarantine(
            "bad-app-factory",
            f"app factory of plugin {spec.name!r} returned "
            f"{type(staged).__name__}, expected cyclopts App",
        )
    return staged


def run_deferred_factory(plugin: RegisteredPlugin) -> App | QuarantineRecord:
    """The plugin's app, running a deferred factory; a failure as a quarantine record.

    The one place a deferred factory runs, for first dispatch and
    ``untaped doctor`` alike. An eager
    plugin returns its staged app without running anything. A plugin
    without an ``app_factory`` has no app to build: asking for one is a
    caller bug and raises :class:`ConfigError`.
    """
    if plugin.app is not None:
        return plugin.app
    factory = plugin.spec.app_factory
    if factory is None:
        raise ConfigError(f"plugin {plugin.spec.name!r} has no commands to build")
    try:
        return _check_factory(plugin.spec, factory)
    except _Quarantine as failed:
        ref = plugin.plugin_ref
        return QuarantineRecord(
            plugin.spec.name, ref.distribution, ref.entry_point, failed.reason, failed.detail
        )


def _commit(
    spec: PluginSpec,
    ref: PluginRef,
    state: _CompositionState,
    app: App | None,
) -> RegisteredPlugin:
    registered = RegisteredPlugin(spec=spec, plugin_ref=ref, skills=tuple(spec.skills), app=app)
    for skill in registered.skills:
        state.skill_names.add(skill.name)
    for check in spec.doctor_checks:
        state.doctor_ids.add(check.id)
    for migration in spec.migrations:
        state.migration_ids.add(migration.id)
    return registered


def resolve_target(target: object) -> object:
    """The object an entry point's ``module:attr`` target names (a non-string passes through)."""
    if not isinstance(target, str):
        return target
    module_name, sep, attr_path = target.partition(":")
    if not sep or not module_name.strip() or not attr_path.strip():
        raise ImportError(f"malformed entry-point target {target!r}: expected 'module:attr'")
    module = import_module(module_name)
    resolved: object = module
    for part in attr_path.split("."):
        resolved = getattr(resolved, part)
    return resolved


def compose(
    shell: ApplicationSpec, candidates: Sequence[PluginCandidate] = ()
) -> CompositionResult:
    """Compose the shell, then every candidate in name order; quarantine failures.

    Each candidate is first provided and validated against the shell alone.
    A plugin name that two or more of the survivors
    claim is contested: every claimant is quarantined, so the result never
    depends on candidate order. The rest commit in ``(name, distribution,
    entry point)`` order, where a skill name or doctor-check id already taken
    quarantines the later one. A spec with ``help`` is deferred: its factory
    runs at first dispatch and in ``untaped doctor`` (:func:`run_deferred_factory`).
    An eager factory runs only after the contest, so a claimant whose factory
    would fail still counts as a claimant.
    """
    state = _CompositionState(shell)
    ordered = sorted(candidates, key=_candidate_order)
    quarantined: dict[int, QuarantineRecord] = {}
    claims: dict[int, PluginSpec] = {}
    for index, candidate in enumerate(ordered):
        try:
            claims[index] = _provide(candidate, state)
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(candidate)
    for index, contest in _contested(ordered, claims).items():
        quarantined[index] = contest.to_record(ordered[index])
        del claims[index]
    plugins: list[RegisteredPlugin] = []
    for index, spec in claims.items():
        candidate = ordered[index]
        try:
            _check_skills(spec, state)
            _check_doctor_checks(spec, state)
            _check_migrations(spec, state)
            factory = spec.app_factory
            staged = (
                None if factory is None or spec.help is not None else _check_factory(spec, factory)
            )
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(candidate)
            continue
        ref = PluginRef(
            distribution=candidate.distribution,
            entry_point=candidate_entry_point(candidate),
            distribution_version=candidate.distribution_version,
            requires_dist=candidate.requires_dist,
        )
        plugins.append(_commit(spec, ref, state, staged))
    return CompositionResult(
        plugins=tuple(plugins),
        quarantine=tuple(quarantined[index] for index in sorted(quarantined)),
        candidates=tuple(ordered),
    )


def _contested(
    ordered: Sequence[PluginCandidate], claims: dict[int, PluginSpec]
) -> dict[int, _Quarantine]:
    """A quarantine per claimant of a plugin name two or more claims share.

    The name is also the config section and CLI group, so one contest
    covers all three.
    """
    claimants: defaultdict[str, list[int]] = defaultdict(list)
    for index, spec in claims.items():
        claimants[spec.name].append(index)
    contested: dict[int, _Quarantine] = {}
    for name, indexes in claimants.items():
        if len(indexes) < 2:
            continue
        claimed_by = ", ".join(
            repr(dist) for dist in sorted(ordered[index].distribution for index in indexes)
        )
        for index in indexes:
            contested[index] = _Quarantine(
                "duplicate-name", f"duplicate plugin name: {name!r} (claimed by {claimed_by})"
            )
    return contested


def _candidate_order(candidate: PluginCandidate) -> tuple[str, str, str]:
    return (candidate.name, candidate.distribution, candidate_entry_point(candidate))


def candidate_entry_point(candidate: PluginCandidate) -> str:
    """The entry point a candidate's plugin records carry: its target, else its name."""
    return candidate.target if isinstance(candidate.target, str) else candidate.name


def _describe(value: object) -> str:
    """How a ``not-a-spec`` detail names what an entry point resolved to."""
    if callable(value):
        return f"the callable {getattr(value, '__qualname__', repr(value))!r}"
    return f"a {type(value).__name__}"


def _module_of(candidate: PluginCandidate) -> str:
    target = candidate.target
    if isinstance(target, str) and ":" in target:
        return target.partition(":")[0]
    return "untaped_" + candidate.name.replace("-", "_")


def _provide(candidate: PluginCandidate, state: _CompositionState) -> PluginSpec:
    """Resolve one candidate's entry point to its ``PluginSpec``, then validate it.

    The entry point names the spec itself (``untaped_acme:SPEC``); anything
    else, a callable included, is ``not-a-spec`` and is never called.

    Raises :class:`_Quarantine` on the first failed check.
    """
    # Metadata-only gates precede any import: group and Requires-Dist
    # admission are decided from distribution metadata without executing
    # plugin code.
    _check_entry_point_group(candidate)
    _check_requires_dist(candidate, state)
    try:
        spec = resolve_target(candidate.target)
    except KindError as exc:
        raise _Quarantine(exc.reason, str(exc), entry_point="") from None
    except Exception as exc:
        raise _Quarantine(
            "malformed-entry-point",
            f"could not resolve entry point {candidate.target!r} of "
            f"distribution {candidate.distribution!r}: {exc}",
            entry_point="",
        ) from None
    if not isinstance(spec, PluginSpec):
        raise _Quarantine(
            "not-a-spec",
            f"entry point {candidate.name!r} of distribution {candidate.distribution!r} "
            f"names {_describe(spec)}, not a PluginSpec; name the module's SPEC constant "
            f"('{_module_of(candidate)}:SPEC')",
        )
    _check_declaration(spec, state)
    if candidate.name != spec.name:
        raise _Quarantine(
            "bad-metadata",
            f"entry-point name {candidate.name!r} does not match plugin name {spec.name!r}",
        )
    if not candidate.distribution.strip():
        raise _Quarantine(
            "bad-metadata",
            f"plugin {candidate.name!r} declares an empty distribution name",
        )
    return spec


def _is_item_store(value: object) -> bool:
    from untaped.sdk.storage.holds import ItemStore  # noqa: PLC0415 - kept off the startup path

    return isinstance(value, ItemStore)
