"""``sdk`` lines: the names an SDK module exports, walked from its ``__all__``.

Each exported name gives lines under the module's path, so a re-export
appears once per module. What a name gives depends on what it is:

- a function: an owner line with its required parameters, and one line per
  optional parameter, ``*args`` and ``**kwargs`` (callbacks carry their arity);
- a class plugins only call (caller shape): its constructor as a function,
  each public method as a function, each public field, property or attribute,
  each class attribute (``class``) and its ``__init_subclass__`` keywords;
- a class plugins implement (``Contract`` subclasses, :data:`IMPLEMENTERS`):
  an ``implement=`` line naming its abstract methods, a line with the full
  typed signature of each of them and of each hook the class defines, plus
  caller lines for the rest of its members;
- an enum: a line per member with its value;
- a ``Literal`` alias: a ``choice=`` line per value;
- any other alias or constant: its name.

Members are those defined on untaped classes: the walk stops at the first
base from another library, and a name such a base defines is never a line.
A plugin module's class stops earlier, at its first exported untaped base,
and names each such base in a ``base=`` line instead: what it inherits is
that base's lines.
Annotations are read in ``annotationlib``'s FORWARDREF format and resolved
against the module and its ``TYPE_CHECKING`` imports, because only a callback's
arity is a promise; an annotation that can't be resolved far enough to tell is
a generation error, never a silent skip.
"""

from __future__ import annotations

import annotationlib
import ast
import collections.abc
import dataclasses
import enum
import functools
import importlib
import inspect
import sys
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from cyclopts import App
from pydantic import BaseModel

from untaped.stability import (
    Deprecated,
    Experimental,
    Stability,
    class_mark,
    function_mark,
    replacement_text,
)
from untaped.surface._lines import DEPRECATED, DeprecatedMark, Line, LineMark
from untaped.surface._lists import HOOKS, IMPLEMENTERS
from untaped.surface._types import default_text, factory_default, literal_values, named_token


class SurfaceError(Exception):
    """Something the walker can't classify: generation stops rather than skip it."""


@dataclass
class Exports:
    """What the SDK walk learned: exported objects by identity, for defaults and contracts."""

    names: dict[int, str] = field(default_factory=dict)
    """``id(object)`` → its exported name, for a default naming an exported object."""
    stable_classes: set[type] = field(default_factory=set)
    """Exported classes that are stable in at least one module."""
    classes: dict[str, type] = field(default_factory=dict)
    """Exported path → class, for the override sweep."""


def line_mark(mark: Stability | None, tree: App | None = None) -> LineMark | None:
    """A stability marker as a line's mark: a deprecation keeps its date and replacement.

    An object replacement is named only against ``tree`` (the root app).
    """
    if isinstance(mark, Experimental):
        return "experimental"
    if isinstance(mark, Deprecated):
        use = replacement_text(mark, tree)
        return DeprecatedMark(mark.removed_in, None if use is None else use.replace("`", ""))
    return None


def own_mark(obj: object) -> LineMark | None:
    """The mark declared on ``obj``: untaped's marker, else ``warnings.deprecated``."""
    target = obj.fget if isinstance(obj, property) else obj
    target = getattr(target, "__func__", target)
    # A class's mark is its own: a subclass of an experimental class is stable.
    found = line_mark(class_mark(target) if isinstance(target, type) else function_mark(target))
    if isinstance(target, type):
        deprecated = vars(target).get("__deprecated__")
    else:
        deprecated = getattr(target, "__deprecated__", None)
    if found is None and deprecated is not None:
        return DEPRECATED
    return found


def collect_exports(modules: Mapping[str, LineMark | None]) -> Exports:
    """The exported names of ``modules`` (module path → the mark its plugin gives it)."""
    exports = Exports()
    for path in sorted(modules):
        module = importlib.import_module(path)
        for name in _exported(module):
            obj = getattr(module, name)
            if not _is_plain(obj):
                exports.names.setdefault(id(obj), name)
            if isinstance(obj, type):
                exports.classes[f"{path}.{name}"] = obj
                if (own_mark(obj) or modules[path]) is None:
                    exports.stable_classes.add(obj)
    return exports


def module_lines(
    path: str, plugin_mark: LineMark | None, exports: Exports, *, plugin: bool = False
) -> list[Line]:
    """Every ``sdk`` line of module ``path``; ``plugin_mark`` is its plugin's mark.

    A ``plugin`` module's class lists only its own members, and a ``base=``
    line per exported untaped base: what it inherits is that base's lines.
    """
    module = importlib.import_module(path)
    walker = _Walker(module, exports, plugin=plugin)
    lines: list[Line] = []
    for name in _exported(module):
        obj = getattr(module, name)
        subject = f"{path}.{name}"
        lines.extend(walker.export(subject, obj, own_mark(obj) or plugin_mark))
    return lines


def _exported(module: types.ModuleType) -> list[str]:
    names = getattr(module, "__all__", None)
    if names is None:
        raise SurfaceError(f"{module.__name__} has no __all__, so its surface can't be listed")
    return sorted(set(names))


def _is_plain(obj: object) -> bool:
    return obj is None or isinstance(obj, str | int | float | bool | tuple | frozenset)


def foreign(cls: type) -> bool:
    """Whether ``cls`` belongs to another library (the MRO walk stops there)."""
    return not cls.__module__.partition(".")[0].startswith("untaped")


def implementer_shaped(cls: type, exports: Exports) -> bool:
    """Whether plugins implement ``cls`` (D8): a ``Contract`` subclass or a listed class."""
    from untaped.contracts import Contract  # noqa: PLC0415

    if issubclass(cls, Contract):
        return True
    return any(exports.classes.get(path) is cls for path in IMPLEMENTERS)


def hooks_of(cls: type, exports: Exports) -> list[str]:
    """The hooks :data:`HOOKS` lists for ``cls`` or an exported base it subclasses."""
    found: set[str] = set()
    for path, members in HOOKS.items():
        base = exports.classes.get(path)
        if base is not None and issubclass(cls, base):
            found.update(members)
    return sorted(found)


class _Walker:
    def __init__(self, module: types.ModuleType, exports: Exports, *, plugin: bool) -> None:
        self.module = module
        self.exports = exports
        self.plugin = plugin
        self.stop = frozenset(exports.classes.values()) if plugin else frozenset[type]()

    def bases(self, subject: str, cls: type, mark: LineMark | None) -> list[Line]:
        """A plugin class's ``base=`` lines: each exported untaped class it subclasses."""
        if not self.plugin:
            return []
        paths: dict[type, list[str]] = {}
        for path, klass in self.exports.classes.items():
            paths.setdefault(klass, []).append(path)
        return [
            Line(f"sdk {subject} base={min(paths[base])}", mark)
            for base in cls.__mro__[1:]
            if base in paths and not foreign(base)
        ]

    def export(self, subject: str, obj: object, mark: LineMark | None) -> list[Line]:
        if isinstance(obj, type) and issubclass(obj, enum.Enum):
            return self.enum(subject, obj, mark)
        if isinstance(obj, type):
            return self.cls(subject, obj, mark)
        if inspect.isfunction(obj) or inspect.isbuiltin(obj) or isinstance(obj, functools.partial):
            return self.function(subject, obj, mark, module=_module_of(obj, self.module))
        if isinstance(obj, types.ModuleType):
            raise SurfaceError(f"{subject} exports a module; export its names instead")
        if isinstance(obj, typing.TypeAliasType):
            values = literal_values(obj.__value__)
        else:
            values = literal_values(obj)
        if values is not None:
            return [Line(f"sdk {subject} choice={_choice(v)}", mark) for v in values]
        return [Line(f"sdk {subject}", mark)]

    def enum(self, subject: str, cls: type[enum.Enum], mark: LineMark | None) -> list[Line]:
        lines = [
            Line(f"sdk {subject}.{member.name} = {default_text(member.value)}", mark)
            for member in cls
        ]
        lines.extend(self.members(subject, cls, mark, skip=set(cls.__members__)))
        return lines

    def cls(self, subject: str, cls: type, mark: LineMark | None) -> list[Line]:
        lines = self.bases(subject, cls, mark)
        if implementer_shaped(cls, self.exports):
            return [*lines, *self.implementer(subject, cls, mark)]
        if _is_protocol(cls):
            # A protocol is never constructed: what it promises is its members.
            return [*lines, Line(f"sdk {subject}", mark), *self.members(subject, cls, mark)]
        lines.extend(self.function(subject, cls, mark, module=_module_of(cls, self.module)))
        lines.extend(self.members(subject, cls, mark))
        return lines

    def implementer(self, subject: str, cls: type, mark: LineMark | None) -> list[Line]:
        """``implement=`` names the abstract members; each of those, and each hook the
        class defines itself, gets one line with its full typed signature."""
        abstract = sorted(getattr(cls, "__abstractmethods__", ()))
        if _is_protocol(cls):
            abstract = sorted(
                name
                for name in typing.get_protocol_members(cls)
                if callable(inspect.getattr_static(cls, name, None))
            )
        lines = [Line(f"sdk {subject} implement={','.join(abstract)}", mark)]
        own, _outside = _split_mro(cls, stop=self.stop)
        typed = sorted(
            name
            for name in {*abstract, *hooks_of(cls, self.exports)}
            if any(name in vars(klass) for klass in own)
        )
        for name in typed:
            member = inspect.getattr_static(cls, name)
            member_mark = own_mark(member) or mark
            lines.append(Line(f"sdk {subject}.{name}{self.typed(cls, name, member)}", member_mark))
        lines.extend(self.members(subject, cls, mark, skip={*abstract, *typed}))
        return lines

    def typed(self, cls: type, name: str, member: object) -> str:
        """A method's full signature with types: ``(a: string, *, b: integer = 1) -> T``."""
        function = _function_of(member)
        if function is None:
            raise SurfaceError(f"{cls.__qualname__}.{name} is a hook but not a method")
        signature = inspect.signature(function)
        module = _module_of(function, self.module)
        scope = {**_type_params(cls), **_type_params(function)}
        hints = {key: _resolved(hint, module, scope) for key, hint in self.hints(function).items()}
        parts: list[str] = []
        parameters = list(signature.parameters.values())
        if not isinstance(member, staticmethod):
            parameters = parameters[1:]
        star = False
        for index, parameter in enumerate(parameters):
            if parameter.kind is parameter.KEYWORD_ONLY and not star:
                parts.append("*")
                star = True
            text = parameter.name
            if parameter.kind is parameter.VAR_POSITIONAL:
                text, star = f"*{text}", True
            elif parameter.kind is parameter.VAR_KEYWORD:
                text = f"**{text}"
            if parameter.name in hints:
                text += f": {named_token(hints[parameter.name]).token}"
            if parameter.default is not parameter.empty:
                text += f" = {default_text(parameter.default, names=self.exports.names)}"
            parts.append(text)
            later = parameters[index + 1 :]
            if parameter.kind is parameter.POSITIONAL_ONLY and not any(
                p.kind is p.POSITIONAL_ONLY for p in later
            ):
                parts.append("/")
        returns = named_token(hints["return"]).token if "return" in hints else "None"
        return f"({', '.join(parts)}) -> {returns}"

    def members(
        self, subject: str, cls: type, mark: LineMark | None, *, skip: set[str] | None = None
    ) -> list[Line]:
        skip = set(skip or ())
        lines: list[Line] = []
        seen: set[str] = set(skip)
        own, outside = _split_mro(cls, stop=self.stop)
        fields = _field_names(cls)
        for klass in own:
            annotations = annotationlib.get_annotations(
                klass, format=annotationlib.Format.FORWARDREF
            )
            for name in [*vars(klass), *annotations]:
                if name in seen:
                    continue
                seen.add(name)
                if name in outside and name not in fields:
                    continue
                if name == "__init_subclass__":
                    lines.extend(self.init_subclass(subject, klass, mark))
                    continue
                if name.startswith("_"):
                    continue
                lines.extend(self.member(subject, klass, name, annotations, mark, fields))
        return lines

    def member(
        self,
        subject: str,
        klass: type,
        name: str,
        annotations: Mapping[str, Any],
        mark: LineMark | None,
        fields: set[str],
    ) -> list[Line]:
        where = f"{subject}.{name}"
        if name not in vars(klass):
            hint = annotations[name]
            is_class = get_origin(hint) is typing.ClassVar or (
                isinstance(hint, annotationlib.ForwardRef)
                and hint.__forward_arg__.startswith("ClassVar")
            )
            return [Line(f"sdk {where} class" if is_class else f"sdk {where}", mark)]
        value = vars(klass)[name]
        member_mark = own_mark(value) or mark
        if isinstance(value, property | functools.cached_property):
            return [Line(f"sdk {where}", member_mark)]
        if isinstance(value, staticmethod | classmethod) or inspect.isfunction(value):
            function = _function_of(value)
            assert function is not None
            return self.function(
                where,
                function,
                member_mark,
                module=_module_of(function, self.module),
                skip_first=not isinstance(value, staticmethod),
                owner=klass,
            )
        if name in fields or inspect.ismemberdescriptor(value):
            return [Line(f"sdk {where}", member_mark)]
        return [Line(f"sdk {where} class", member_mark)]

    def init_subclass(self, subject: str, klass: type, mark: LineMark | None) -> list[Line]:
        hook = vars(klass)["__init_subclass__"]
        function = _function_of(hook)
        if function is None:
            raise SurfaceError(f"{klass.__qualname__}.__init_subclass__ is not a function")
        lines = self.function(
            f"{subject}.__init_subclass__",
            function,
            mark,
            module=_module_of(function, self.module),
            skip_first=True,
            owner=klass,
        )
        # A subclass's keywords are the promise; **kwargs only passes the rest on.
        empty = f"sdk {subject}.__init_subclass__()"
        return [
            line for line in lines if not line.text.endswith("(**kwargs)") and line.text != empty
        ]

    def function(
        self,
        subject: str,
        function: Callable[..., Any] | type,
        mark: LineMark | None,
        *,
        module: types.ModuleType,
        skip_first: bool = False,
        owner: type | None = None,
    ) -> list[Line]:
        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError) as exc:
            raise SurfaceError(f"{subject}: its signature can't be read ({exc})") from None
        hints = self.hints(function)
        scope = {**_type_params(owner), **_type_params(function)}
        factories = _factories(function)
        parameters = list(signature.parameters.values())
        if skip_first:
            parameters = parameters[1:]
        lines: list[Line] = []
        required: list[str] = []
        keywords: list[str] = []
        callbacks: list[str] = []
        last_only = max(
            (
                index
                for index, p in enumerate(parameters)
                if p.kind is p.POSITIONAL_ONLY and p.default is p.empty
            ),
            default=None,
        )
        position = 0
        for index, parameter in enumerate(parameters):
            if parameter.kind is parameter.VAR_POSITIONAL:
                lines.append(Line(f"sdk {subject}(*{parameter.name})", mark))
                continue
            if parameter.kind is parameter.VAR_KEYWORD:
                lines.append(Line(f"sdk {subject}(**{parameter.name})", mark))
                continue
            arity = self.arity(subject, parameter, hints, module, scope)
            name = parameter.name
            if parameter.kind is not parameter.KEYWORD_ONLY:
                position += 1
                # ``name@k/=``: positional-only, so a keyword caller can't pass it.
                only = "/" if parameter.kind is parameter.POSITIONAL_ONLY else ""
                name = f"{name}@{position}{only}"
            if parameter.default is not parameter.empty:
                lines.append(self.optional(subject, name, parameter, arity, mark, factories))
                continue
            if parameter.kind is parameter.KEYWORD_ONLY:
                keywords.append(parameter.name)
            else:
                required.extend([parameter.name, "/"] if index == last_only else [parameter.name])
            if arity is not None:
                callbacks.append(f"callback({parameter.name}, {arity})")
        items = [*required, *(["*", *keywords] if keywords else [])]
        text = f"sdk {subject}({', '.join(items)})"
        if callbacks:
            text += " " + " ".join(callbacks)
        return [Line(text, mark), *lines]

    def optional(
        self,
        subject: str,
        name: str,
        parameter: inspect.Parameter,
        arity: str | None,
        mark: LineMark | None,
        factories: Mapping[str, object],
    ) -> Line:
        default = parameter.default
        if parameter.name in factories:
            default = factory_default(factories[parameter.name])
        text = f"sdk {subject}({name}=) default={self.default(default)}"
        if arity is not None:
            text += f" callback({arity})"
        return Line(text, mark)

    def default(self, value: object) -> str:
        return default_text(value, names=self.exports.names)

    def hints(self, function: object) -> dict[str, Any]:
        target = function.__init__ if isinstance(function, type) else function  # type: ignore[misc]
        target = inspect.unwrap(target) if callable(target) else target
        if isinstance(function, type) and issubclass(function, BaseModel):
            return {name: f.annotation for name, f in function.model_fields.items()}
        if isinstance(function, type) and dataclasses.is_dataclass(function):
            found = annotationlib.get_annotations(function, format=annotationlib.Format.FORWARDREF)
            return {
                **_dataclass_hints(function),
                **{k: v for k, v in found.items() if not _is_classvar(v)},
            }
        try:
            return dict(
                annotationlib.get_annotations(target, format=annotationlib.Format.FORWARDREF)
            )
        except TypeError:
            return {}

    def arity(
        self,
        subject: str,
        parameter: inspect.Parameter,
        hints: Mapping[str, Any],
        module: types.ModuleType,
        scope: Mapping[str, object],
    ) -> str | None:
        """The callback arity of ``parameter`` (``N``, ``*`` or ``P``), or ``None``."""
        if parameter.name not in hints:
            return None
        try:
            return _arity(hints[parameter.name], module, scope)
        except _Unresolved as exc:
            raise SurfaceError(
                f"{subject}({parameter.name}): its annotation {exc} can't be resolved, so "
                "the walker can't tell whether it is a callback"
            ) from None


def _factories(function: object) -> dict[str, object]:
    """The default factories of a dataclass's or model's fields, by parameter name."""
    if isinstance(function, type) and issubclass(function, BaseModel):
        return {
            name: info.default_factory
            for name, info in function.model_fields.items()
            if info.default_factory is not None
        }
    if isinstance(function, type) and dataclasses.is_dataclass(function):
        return {
            f.name: f.default_factory
            for f in dataclasses.fields(function)
            if f.default_factory is not dataclasses.MISSING
        }
    return {}


def _resolved(hint: object, module: types.ModuleType, scope: Mapping[str, object]) -> object:
    """``hint`` with a forward reference evaluated where it can be; as it is otherwise."""
    if not isinstance(hint, annotationlib.ForwardRef | str):
        return hint
    text = hint if isinstance(hint, str) else hint.__forward_arg__
    try:
        return eval(text, {**vars(module), **_type_checking_names(module), **scope})
    except Exception:
        return hint


def _type_params(obj: object) -> dict[str, object]:
    return {param.__name__: param for param in getattr(obj, "__type_params__", ())}


def _function_of(member: object) -> Callable[..., Any] | None:
    if isinstance(member, staticmethod | classmethod):
        return typing.cast("Callable[..., Any]", member.__func__)
    if inspect.isfunction(member):
        return member
    return None


def _module_of(obj: object, fallback: types.ModuleType) -> types.ModuleType:
    """The module whose names ``obj``'s annotations use (a class's: its ``__init__``'s)."""
    if isinstance(obj, type):
        init = inspect.unwrap(obj.__init__)  # type: ignore[misc]
        if inspect.isfunction(init) and not dataclasses.is_dataclass(obj):
            obj = init
    found = sys.modules.get(getattr(inspect.unwrap(obj), "__module__", "") or "")  # type: ignore[arg-type]
    return found if found is not None else fallback


def _split_mro(cls: type, *, stop: frozenset[type] = frozenset()) -> tuple[list[type], set[str]]:
    """The untaped classes of ``cls``'s MRO before the first foreign one or one in ``stop``,
    and the names the foreign classes define (never lines: another library's API)."""
    own: list[type] = []
    outside: set[str] = set()
    stopped = False
    for klass in cls.__mro__:
        if klass is object:
            continue
        if foreign(klass) or klass is typing.Generic or _is_protocol_base(klass):
            stopped = True
            outside.update(vars(klass))
            continue
        if klass is not cls and klass in stop:
            stopped = True
        if not stopped:
            own.append(klass)
    return own, outside


def _is_protocol_base(klass: type) -> bool:
    return klass.__module__ in ("typing", "abc", "builtins")


def _is_protocol(cls: type) -> bool:
    return bool(getattr(cls, "_is_protocol", False))


def _field_names(cls: type) -> set[str]:
    if isinstance(cls, type) and issubclass(cls, BaseModel):
        return set(cls.model_fields)
    if dataclasses.is_dataclass(cls):
        return {f.name for f in dataclasses.fields(cls)}
    return set()


def _dataclass_hints(cls: type) -> dict[str, Any]:
    hints: dict[str, Any] = {}
    for klass in reversed(cls.__mro__):
        if dataclasses.is_dataclass(klass):
            hints.update(
                annotationlib.get_annotations(klass, format=annotationlib.Format.FORWARDREF)
            )
    return hints


def _is_classvar(hint: object) -> bool:
    if get_origin(hint) is typing.ClassVar or hint is typing.ClassVar:
        return True
    return isinstance(hint, annotationlib.ForwardRef) and hint.__forward_arg__.startswith(
        "ClassVar"
    )


def _choice(value: object) -> str:
    from untaped.surface._types import choice_text  # noqa: PLC0415

    return choice_text(value)


# --- callbacks ----------------------------------------------------------------


class _Unresolved(Exception):
    """An annotation the walker can't resolve far enough to tell whether it is a callback."""


def _arity(annotation: object, module: types.ModuleType, scope: Mapping[str, object]) -> str | None:
    """The arity a callback annotation promises, or ``None`` when it is not a callback.

    ``Callable[[A, B], R]`` is ``2``; ``Callable[..., R]`` is ``*``; a ParamSpec
    callable is ``P``; a Protocol with ``__call__`` counts its parameters
    after ``self``. A callable inside a container or a union counts for its
    owner.
    """
    annotation = _resolve(annotation, module, scope)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is typing.Literal:
        return None
    if annotation is collections.abc.Callable or annotation is typing.Callable:
        return "*"
    if origin is collections.abc.Callable:
        params = args[0] if args else Ellipsis
        if params is Ellipsis:
            return "*"
        if isinstance(params, typing.ParamSpec) or get_origin(params) is typing.Concatenate:
            return "P"
        return str(len(params))
    if origin is typing.Annotated:
        return _arity(args[0], module, scope)
    if isinstance(annotation, type) and _is_protocol(annotation) and "__call__" in dir(annotation):
        call = inspect.getattr_static(annotation, "__call__", None)
        if inspect.isfunction(call) and "__call__" in typing.get_protocol_members(annotation):
            parameters = list(inspect.signature(call).parameters.values())[1:]
            if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in parameters):
                return "*"
            return str(
                sum(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in parameters)
            )
        return None
    found = {
        each
        for arg in args
        if not isinstance(arg, list) and arg is not Ellipsis
        for each in [_arity(arg, module, scope)]
        if each is not None
    }
    if len(found) > 1:
        raise _Unresolved(f"{annotation!r} (callables of different arities)")
    return found.pop() if found else None


def _resolve(annotation: object, module: types.ModuleType, scope: Mapping[str, object]) -> object:
    if not isinstance(annotation, annotationlib.ForwardRef | str):
        return annotation
    text = annotation if isinstance(annotation, str) else annotation.__forward_arg__
    namespace = {**vars(module), **_type_checking_names(module), **scope}
    try:
        found = eval(text, namespace)
    except Exception:
        tree = ast.parse(text, mode="eval").body
        return _shape(tree, text, namespace)
    # A quoted annotation under ``from __future__ import annotations`` evaluates to its text.
    return _resolve(found, module, scope) if isinstance(found, str) else found


def _shape(tree: ast.expr, text: str, namespace: Mapping[str, object]) -> object:
    """Enough of an annotation to tell a callback: its outer name and argument count."""
    if isinstance(tree, ast.Subscript):
        outer = _name(tree.value, namespace)
        if outer is collections.abc.Callable or outer is typing.Callable:
            first = tree.slice.elts[0] if isinstance(tree.slice, ast.Tuple) else tree.slice
            if isinstance(first, ast.List):
                return collections.abc.Callable[[object] * len(first.elts), object]
            if isinstance(first, ast.Constant) and first.value is Ellipsis:
                return collections.abc.Callable[..., object]
    if isinstance(tree, ast.BinOp) and isinstance(tree.op, ast.BitOr):
        left = _shape(tree.left, text, namespace)
        right = _shape(tree.right, text, namespace)
        return _first_callable(left, right)
    raise _Unresolved(repr(text))


def _first_callable(*options: object) -> object:
    for option in options:
        if get_origin(option) is collections.abc.Callable:
            return option
    return None


def _name(tree: ast.expr, namespace: Mapping[str, object]) -> object:
    try:
        return eval(compile(ast.Expression(tree), "<annotation>", "eval"), dict(namespace))
    except Exception:
        return None


@functools.cache
def _type_checking_names(module: types.ModuleType) -> dict[str, object]:
    """The names a module imports under ``if TYPE_CHECKING:``, imported here."""
    try:
        source = inspect.getsource(module)
    except OSError, TypeError:
        return {}
    names: dict[str, object] = {}
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.If) and _is_type_checking(node.test)):
            continue
        for statement in node.body:
            if isinstance(statement, ast.Import | ast.ImportFrom):
                code = compile(ast.Module([statement], []), module.__name__, "exec")
                scope: dict[str, object] = {"__name__": module.__name__}
                if module.__package__:
                    scope["__package__"] = module.__package__
                exec(code, scope)
                names.update({k: v for k, v in scope.items() if not k.startswith("__")})
    return names


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
