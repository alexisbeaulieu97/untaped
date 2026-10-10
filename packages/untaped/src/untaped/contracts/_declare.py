"""Declaring a contract and filling it: what an owner and a provider write.

An owner declares a contract as an ABC subclass of :class:`Contract`; a
provider fills it by subclassing the contract. ``Contract.__init_subclass__``
tells the two apart: a class whose own bases name :class:`Contract` is a
declaration and is checked here, once; any other subclass is a provider (or
an intermediate base of one), whose overrides of ``@bridge`` and ``@cached``
methods are re-wrapped so the owner's declaration holds wherever the method is
called from.
"""

from __future__ import annotations

import builtins
import functools
import inspect
import json
import re
import types
from abc import ABC
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, TypeVar, cast, get_args, get_origin, get_type_hints

from pydantic import (
    BaseModel,
    ConfigDict,
    Secret,
    SecretBytes,
    SecretStr,
    TypeAdapter,
    ValidationError,
)
from pydantic_core import PydanticSerializationError, to_json

from untaped.errors import ConfigError, UntapedError, first_validation_error
from untaped.records import Record, kind_of

if TYPE_CHECKING:
    from untaped.http import HttpClient
    from untaped.plugins.registry import PluginSpec

#: Names a contract method may not take: the provider machinery's own.
SDK_NAMES = frozenset({"providers", "gather", "convert", "on", "own", "ready", "settings", "http"})

_BRIDGE = "__untaped_bridge__"
_CACHED = "__untaped_cached__"
_LISTING = "__untaped_listing__"
_CONTRACT_OF = "__untaped_contract_of__"
_CONTRACT = "__untaped_contract__"
_BINDING = "_untaped_binding"


class ContractError(TypeError):
    """A contract declaration that breaks a rule; ``reason`` is the stable code."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(f"{message} [{reason}]")
        self.reason = reason


class Source(BaseModel):
    """Where an issued record came from: the provider's plugin and its own record.

    ``record`` is the JSON dump of the provider's own record when its item type
    is not the owner's model, else empty. The SDK fills it; a provider never does.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plugin: str
    kind: str
    record: dict[str, Any] = {}


class Issued(Record):
    """A record a provider issues on an owner's behalf; ``source`` says which provider."""

    source: Source | None = None


@dataclass(frozen=True)
class NotReady:
    """Why a provider can't answer in this profile (``Contract.ready``)."""

    reason: str
    setting: str | None = None


@dataclass(frozen=True)
class Method:
    """One method a contract declares."""

    name: str
    function: Callable[..., Any]
    #: The resolved annotations, ``T`` left as the contract's type parameter.
    hints: Mapping[str, Any]
    bridge: bool
    max_age: timedelta | None
    #: Declared ``@listing``: an invalid row is dropped and the rest kept.
    #: Any other method's invalid item fails the answer.
    listing: bool


@dataclass(frozen=True)
class ContractInfo:
    """What the SDK knows about a declared contract."""

    cls: type[Contract]
    #: snake_case of the class: unique per owner, used in config and kinds.
    name: str
    shell: bool
    #: The owner's model, the default of the item type parameter (``T``);
    #: ``None`` for a contract without one.
    item: type[Issued] | None
    item_param: TypeVar | None
    methods: Mapping[str, Method]


@dataclass(frozen=True)
class Binding:
    """What the registry tells a provider instance about itself."""

    spec: PluginSpec
    owner: str
    contract: ContractInfo
    #: The provider's own item type ``T`` (the owner's model when it has none).
    item: type[Record] | None
    #: The profile the provider was bound for.
    profile: str

    @property
    def plugin(self) -> str:
        return self.spec.name

    @property
    def owns_item(self) -> bool:
        """Whether the provider's ``T`` is the owner's model (nothing to bridge)."""
        return self.item is None or self.item is self.contract.item


def snake_case(name: str) -> str:
    """``RepoSource`` → ``repo_source``, ``HTTPSource`` → ``http_source``."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def bridge[F: Callable[..., Any]](fn: F, /) -> F:
    """Declare a method that turns a provider's own record into the owner's model.

    On the owner's contract it is a declaration; every provider's override is
    re-wrapped with it, so ``self.to_repo(item)`` inside a provider is stamped
    too. The wrapper stamps ``source`` (plugin, the item's kind and its JSON
    dump) and validates nothing: the SDK validates once, after the call.
    """
    if getattr(fn, _BRIDGE, None) is not None:
        return fn

    @functools.wraps(fn)
    def stamped(self: Contract, item: Any, /, *args: Any, **kwargs: Any) -> Any:
        return _stamp(self, item, fn(self, item, *args, **kwargs))

    setattr(stamped, _BRIDGE, fn)
    return stamped  # type: ignore[return-value]  # same signature as fn


def listing[F: Callable[..., Any]](fn: F, /) -> F:
    """Declare a method that returns a list of rows, each standing alone.

    An invalid row is dropped (and reported in ``Ok.invalid``) and the rest
    kept; without it, one invalid item fails the provider's whole answer.
    The method must return a ``list``; checked at declaration.
    """
    setattr(fn, _LISTING, True)
    return fn


def cached[F: Callable[..., Any]](*, max_age: timedelta) -> Callable[[F], F]:
    """Declare that a method's answers are kept for ``max_age`` (the answer cache).

    The owner declares it; the SDK keeps one entry per provider, method,
    profile and arguments under ``~/.untaped/plugins/<provider>/cache/``. A
    live call that fails while an entry exists serves the entry, marked stale.
    """
    if max_age <= timedelta(0):
        raise TypeError("cached(max_age=...) needs a positive timedelta")

    def mark(fn: F) -> F:
        return _served(fn, max_age, fn.__name__)

    return mark


def _served[F: Callable[..., Any]](fn: F, max_age: timedelta, name: str) -> F:
    """``fn`` served through the answer cache as contract method ``name``."""
    if getattr(fn, _CACHED, None) is not None:
        return fn

    @functools.wraps(fn)
    def served(self: Contract, /, *args: Any, **kwargs: Any) -> Any:
        from untaped.contracts._cache import call  # noqa: PLC0415 - cache needs settings

        return call(self, name, fn, max_age, args, kwargs)

    setattr(served, _CACHED, max_age)
    return served  # type: ignore[return-value]  # same signature as fn


def _stamp(provider: Contract, item: Any, result: Any) -> Any:
    if not isinstance(result, Issued):
        return result  # the SDK's validation after the call names the problem
    binding = binding_of(provider)
    if binding is None:
        raise UntapedError(
            f"{type(provider).__qualname__} is not bound to a plugin, so it can't stamp a source",
            hint="ask for providers through untaped.contracts (gather, convert)",
        )
    if binding.owns_item or not isinstance(item, BaseModel):
        kind = kind_of(type(result)) or ""
        record: dict[str, Any] = {}
    else:
        kind = kind_of(type(item)) or ""
        record = item.model_dump(mode="json", by_alias=True, round_trip=True)
    source = Source(plugin=binding.plugin, kind=kind, record=record)
    return result.model_copy(update={"source": source})


def binding_of(provider: object) -> Binding | None:
    """The registry's binding of a provider instance (``None``: not from the registry)."""
    found = vars(provider).get(_BINDING) if hasattr(provider, "__dict__") else None
    return found if isinstance(found, Binding) else None


def bind(provider: Contract, binding: Binding) -> None:
    """Record ``binding`` on ``provider`` (the registry does it once per instance)."""
    vars(provider)[_BINDING] = binding


class Contract(ABC):  # noqa: B024 - every method has a default
    """Base of every contract and, through it, of every provider.

    An owner declares a contract by subclassing this directly, with each
    required method ``@abstractmethod`` and optional ones raising
    ``NotImplementedError``; ``shell=`` is reserved for shell plugins (not in
    11.0). The declaration is checked when the class is created: method names
    may not shadow builtins or SDK names, and every parameter and return type
    must be pydantic-serialisable (``unserialisable-signature``). A provider
    subclasses the contract; what it fills is the methods it overrides.
    """

    def __init_subclass__(cls, *, shell: bool | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if Contract in cls.__bases__:
            setattr(cls, _CONTRACT, _declare(cls, shell=bool(shell)))
            return
        if shell is not None:
            raise TypeError(f"{cls.__qualname__}: shell= is a contract's keyword, not a provider's")
        _rewrap(cls)

    def ready(self) -> NotReady | None:
        """``None`` when this provider can answer in the active profile, else why not.

        The default: a :class:`Configured` provider is ready when its settings
        validate; any other provider always is. Override only for a condition
        a settings schema can't express.
        """
        if isinstance(self, Configured):
            return self._settings_ready()
        return None

    @property
    def http(self) -> HttpClient:
        """The SDK's HTTP client for this provider: the profile's proxy, CA and timeout.

        Inside ``gather(deadline=...)`` no request starts past the deadline
        (true of every untaped ``HttpClient``; another library's is not bounded).
        """
        return _per_profile(self, "_untaped_http", lambda: _http_client(binding_of(self)))

    def own(self, item: Issued) -> Record:
        """The provider's own record ``item`` was issued from (rehydrated from ``source``)."""
        binding = binding_of(self)
        if binding is None or binding.owns_item or binding.item is None:
            return item
        source = item.source
        if source is None or source.plugin != binding.plugin:
            raise UntapedError(
                f"this record was not issued by {binding.plugin}",
                category="invalid",
                system=binding.plugin,
            )
        try:
            return binding.item.model_validate_json(json.dumps(source.record), strict=True)
        except ValidationError as exc:
            raise UntapedError(
                f"this {kind_of(binding.item)} record no longer reads: "
                f"{first_validation_error(exc)}",
                category="invalid",
                system=binding.plugin,
                hint=f"ask {binding.plugin} for it again; its record shape changed since",
            ) from None


def _per_profile[V](holder: object, attribute: str, load: Callable[[], V]) -> V:
    """``load()``, kept on ``holder`` while its binding holds.

    The registry binds a provider afresh for each composition and profile, so
    an instance a factory hands out twice never serves another profile's value.
    """
    binding = binding_of(holder)
    held = vars(holder).get(attribute)
    if held is not None and held[0] is binding:
        return held[1]  # type: ignore[no-any-return]  # stored below
    value = load()
    vars(holder)[attribute] = (binding, value)
    return value


def _http_client(binding: Binding | None) -> HttpClient:
    from untaped.http import HttpClient, RetryPolicy, resolve_verify  # noqa: PLC0415
    from untaped.settings import HttpSettings, load_settings_section  # noqa: PLC0415

    http = load_settings_section("http")
    if not isinstance(http, HttpSettings):
        http = HttpSettings.model_validate(http)
    return HttpClient(
        verify=resolve_verify(http),
        timeout=http.timeout_seconds,
        proxy=http.proxy,
        retry=RetryPolicy(),
        system=None if binding is None else binding.plugin,
    )


class Configured[S: BaseModel]:
    """Mixin giving a provider its plugin's settings as ``self.settings``.

    ``S`` is the plugin's settings model (``PluginSpec.settings``), read for
    the active profile on first use. ``Contract.ready`` counts the provider
    ready when they validate.
    """

    @property
    def settings(self) -> S:
        """The plugin's settings section for the active profile."""
        settings = _per_profile(self, "_untaped_settings", lambda: _load_settings(self))
        return cast("S", settings)

    def _settings_ready(self) -> NotReady | None:
        try:
            self.settings  # noqa: B018 - loading is the check
        except ConfigError as exc:
            cause = exc.__cause__
            setting = None
            if isinstance(cause, ValidationError) and cause.errors():
                loc = ".".join(str(part) for part in cause.errors()[0]["loc"])
                binding = binding_of(self)
                setting = loc if binding is None else f"{binding.plugin}.{loc}"
            return NotReady(str(exc), setting=setting)
        return None


def settings_type(cls: type) -> type[BaseModel] | None:
    """``S`` of ``Configured[S]`` in ``cls``'s bases (``None``: not configured, or unresolved)."""
    found = _base_argument(cls, Configured)
    return found if isinstance(found, type) and issubclass(found, BaseModel) else None


def _load_settings(provider: Configured[Any]) -> BaseModel:
    from untaped.settings import get_config_section  # noqa: PLC0415

    binding = binding_of(provider)
    model = settings_type(type(provider))
    if binding is None or model is None:
        raise ConfigError(
            f"{type(provider).__qualname__} has no plugin settings to read "
            "(it is not bound to a plugin, or its Configured[...] argument is not a model)"
        )
    return get_config_section(binding.plugin, model)


_UNSET: Any = object()


def _base_argument(cls: type, generic: type) -> Any:
    """The argument ``cls`` gives ``generic``'s first type parameter, looking up its bases.

    The first class in ``cls``'s MRO that names ``generic`` among its own
    bases decides: ``generic[X]`` gives ``X`` (a ``TypeVar`` when the base
    passes its own parameter on, which v1 does not resolve), a bare
    ``generic`` gives :data:`_UNSET`. ``None`` when no class names it.
    """
    for klass in cls.__mro__:
        for base in types.get_original_bases(klass):
            origin = get_origin(base) or base
            if origin is not generic:
                continue
            args = get_args(base)
            return args[0] if args else _UNSET
    return None


def item_type(provider: type[Contract], contract: ContractInfo) -> type[Record] | None:
    """The provider's ``T``: its direct parametrisation of the contract, else the default.

    Raises :class:`ContractError` (``unresolved-item-type``) when a base passes
    a type parameter on (v1 resolves only a direct ``Contract[X]`` or a bare
    ``Contract``) or ``X`` is not a :class:`Record` with a kind.
    """
    if contract.item_param is None:
        return None
    found = _base_argument(provider, contract.cls)
    if found is _UNSET or found is None:
        return contract.item
    if isinstance(found, TypeVar):
        raise ContractError(
            f"{provider.__qualname__} reaches {contract.cls.__qualname__} through a generic base; "
            f"subclass {contract.cls.__qualname__}[YourRecord] directly",
            reason="unresolved-item-type",
        )
    if not (isinstance(found, type) and issubclass(found, Record)):
        raise ContractError(
            f"{provider.__qualname__}: {found!r} is not a Record", reason="unresolved-item-type"
        )
    if found is not contract.item and kind_of(found) is None:
        raise ContractError(
            f"{provider.__qualname__}: {found.__qualname__} declares no kind, "
            "so a piped record can't find its way back to it",
            reason="unresolved-item-type",
        )
    return found


def contract_of(cls: type) -> ContractInfo | None:
    """The contract ``cls`` declares or fills (``None``: not a contract class)."""
    for klass in cls.__mro__:
        info = vars(klass).get(_CONTRACT)
        if isinstance(info, ContractInfo):
            return info
    return None


def method_contract(method: Callable[..., Any]) -> tuple[ContractInfo, Method]:
    """The contract and method a ``Contract.method`` reference names."""
    cls = getattr(method, _CONTRACT_OF, None)
    info = None if cls is None else contract_of(cls)
    name = getattr(method, "__name__", "")
    if info is None or name not in info.methods:
        raise TypeError(
            f"{method!r} is not a contract method: pass it as Contract.method (RepoSource.repos)"
        )
    return info, info.methods[name]


def fills(provider: type[Contract], contract: ContractInfo, name: str) -> bool:
    """Whether ``provider`` overrides the contract's method ``name``."""
    for klass in provider.__mro__:
        if klass is contract.cls:
            return False
        if name in vars(klass):
            return True
    return False


def unused_methods(provider: type[Contract], contract: ContractInfo) -> list[str]:
    """Public methods ``provider`` defines that the contract doesn't have (removed or misspelt)."""
    unused: list[str] = []
    for klass in provider.__mro__:
        if klass is contract.cls:
            break
        for name, value in vars(klass).items():
            if (
                not name.startswith("_")
                and inspect.isfunction(value)
                and name not in contract.methods
                and name not in SDK_NAMES
                and name not in unused
            ):
                unused.append(name)
    return sorted(unused)


def _rewrap(cls: type[Contract]) -> None:
    """Re-apply the contract's ``@bridge`` and ``@cached`` to what ``cls`` fills.

    A filled method defined on ``cls`` or on a plain mixin it inherits is
    rewrapped onto ``cls``; one inherited from a provider base already is.
    ``@cached`` anywhere in the provider's own code is a ``TypeError``.
    """
    info = contract_of(cls)
    if info is None:
        return
    own = [cls, *(klass for klass in cls.__mro__ if not issubclass(klass, Contract))]
    for klass in own:
        for name, value in vars(klass).items():
            if _carries_cached(value):
                raise TypeError(
                    f"{klass.__qualname__}.{name}: only the contract decides what is @cached"
                )
    for name, method in info.methods.items():
        holder = next(klass for klass in cls.__mro__ if name in vars(klass))
        if holder is not cls and issubclass(holder, Contract):
            continue
        value = vars(holder)[name]
        if not inspect.isfunction(value):
            continue
        wrapped = value
        if method.bridge:
            wrapped = bridge(wrapped)
        if method.max_age is not None:
            wrapped = _served(wrapped, method.max_age, name)
        if wrapped is not value:
            setattr(cls, name, wrapped)


def _carries_cached(value: object) -> bool:
    inner = [value, getattr(value, "__func__", None), getattr(value, "fget", None)]
    return any(getattr(each, _CACHED, None) is not None for each in inner if each is not None)


def _declare(cls: type[Contract], *, shell: bool) -> ContractInfo:
    where = cls.__qualname__
    params = cls.__type_params__
    item_param = params[0] if params and isinstance(params[0], TypeVar) else None
    item = _item_default(where, item_param)
    methods: dict[str, Method] = {}
    for name, value in vars(cls).items():
        if name.startswith("_") or not inspect.isfunction(value):
            continue
        if name in SDK_NAMES or hasattr(builtins, name):
            raise TypeError(
                f"{where}.{name}: a contract method may not be named after "
                f"{'SDK machinery' if name in SDK_NAMES else 'a builtin'} (pick another name)"
            )
        hints = _signature_hints(where, name, value, params)
        max_age = getattr(value, _CACHED, None)
        if max_age is not None and _carries_secret(hints["return"]):
            raise TypeError(
                f"{where}.{name}: @cached would write a secret to disk; "
                "a method returning one is never cached"
            )
        rows = getattr(value, _LISTING, False)
        if rows and get_origin(hints["return"]) is not list:
            raise TypeError(f"{where}.{name}: a @listing method returns a list")
        methods[name] = Method(
            name=name,
            function=value,
            hints=types.MappingProxyType(hints),
            bridge=getattr(value, _BRIDGE, None) is not None,
            max_age=max_age,
            listing=rows,
        )
        setattr(value, _CONTRACT_OF, cls)
    return ContractInfo(
        cls=cls,
        name=snake_case(cls.__name__),
        shell=shell,
        item=item,
        item_param=item_param,
        methods=types.MappingProxyType(methods),
    )


def _item_default(where: str, param: TypeVar | None) -> type[Issued] | None:
    if param is None:
        return None
    default = param.__default__
    if not (isinstance(default, type) and issubclass(default, Issued)):
        raise TypeError(
            f"{where}: the item type parameter {param.__name__} needs the owner's model as its "
            f"default ({param.__name__}: Record = YourModel), an Issued subclass"
        )
    if kind_of(default) is None:
        raise TypeError(f"{where}: the owner's model {default.__qualname__} declares no kind")
    return default


def _signature_hints(
    where: str, name: str, function: Callable[..., Any], params: tuple[Any, ...]
) -> dict[str, Any]:
    """The method's resolved annotations, after the serialisable-signature check."""
    at = f"{where}.{name}"
    scope = {param.__name__: param for param in params}
    try:
        hints = get_type_hints(inspect.unwrap(function), localns=scope, include_extras=True)
    except Exception as exc:
        raise ContractError(
            f"{at}: its annotations don't resolve ({exc})", reason="unserialisable-signature"
        ) from None
    if "return" not in hints:
        raise ContractError(f"{at}: annotate the return type", reason="unserialisable-signature")
    parameters = list(inspect.signature(function).parameters.values())[1:]
    for parameter in parameters:
        label = f"{at}({parameter.name})"
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY):
            raise ContractError(
                f"{label}: *args, **kwargs and positional-only parameters can't become a "
                "request record",
                reason="unserialisable-signature",
            )
        if parameter.name not in hints:
            raise ContractError(f"{label}: annotate it", reason="unserialisable-signature")
        if parameter.default is not parameter.empty and not _serialisable(parameter.default):
            raise ContractError(
                f"{label}: its default {parameter.default!r} is not JSON",
                reason="unserialisable-signature",
            )
    for key, hint in hints.items():
        try:
            TypeAdapter(hint).json_schema()
        except Exception as exc:
            what = "return type" if key == "return" else f"parameter {key!r}"
            raise ContractError(
                f"{at}: the {what} {hint!r} has no JSON schema ({exc})",
                reason="unserialisable-signature",
            ) from None
    return hints


def _serialisable(value: object) -> bool:
    try:
        to_json(value)
    except PydanticSerializationError:
        return False
    return True


def _carries_secret(annotation: object, seen: set[type] | None = None) -> bool:
    seen = set() if seen is None else seen
    origin = get_origin(annotation)
    target = origin if isinstance(origin, type) else annotation
    if isinstance(target, type):
        if issubclass(target, SecretStr | SecretBytes | Secret):
            return True
        if issubclass(target, BaseModel) and target not in seen:
            seen.add(target)
            if any(
                _carries_secret(field.annotation, seen) for field in target.model_fields.values()
            ):
                return True
    return any(_carries_secret(arg, seen) for arg in get_args(annotation))
