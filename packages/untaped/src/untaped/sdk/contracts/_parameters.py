"""A provider method's parameters against its contract method's: checked, and adapted.

A provider released against an older owner may declare fewer parameters than
the contract method has now: core binds each call to the contract's signature,
applies its defaults, and passes the provider only the names it declares, by
keyword (everything, when it takes ``**kwargs``). A parameter the contract
doesn't declare, or one the provider takes positional-only, can't be passed
that way, so it is a problem the registry quarantines that provider for,
never an error raised while the plugin's module imports.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


def parameter_problem(provider: Callable[..., Any], contract: Callable[..., Any]) -> str | None:
    """Why ``provider`` can't take its arguments by name from ``contract``'s; ``None``: it can.

    Both are the functions as defined, ``self`` first.
    """
    declared = {
        parameter.name for parameter in _parameters(contract) if parameter.kind not in _VARIADIC
    }
    params = _parameters(provider)
    keywords = any(parameter.kind is parameter.VAR_KEYWORD for parameter in params)
    for parameter in params:
        if parameter.kind is parameter.VAR_POSITIONAL and not keywords:
            return f"takes *{parameter.name} without **kwargs, and every argument is passed by name"
        if parameter.kind in _VARIADIC:
            continue
        if parameter.kind is parameter.POSITIONAL_ONLY:
            return f"takes {parameter.name!r} positional-only, and it is passed by name"
        if parameter.name not in declared:
            return f"takes {parameter.name!r}, which the contract method doesn't declare"
    return None


def adapted[F: Callable[..., Any]](provider: F, contract: Callable[..., Any]) -> F:
    """``provider`` called as ``contract`` is: bound to its signature, with its defaults.

    ``provider`` itself when it already has ``contract``'s parameters, kinds and
    defaults. The adapter reports ``contract``'s signature, so what reads a
    call's arguments (an answer cache's key) reads the contract's.
    """
    if _shape(provider) == _shape(contract):
        return provider
    signature = inspect.signature(contract)
    params = _parameters(provider)
    everything = any(parameter.kind is parameter.VAR_KEYWORD for parameter in params)
    names = [parameter.name for parameter in params if parameter.kind not in _VARIADIC]

    @functools.wraps(provider)
    def adapter(*args: Any, **kwargs: Any) -> Any:
        # a contract method has no variadic or positional-only parameter (_declare refuses them)
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        (_, instance), *arguments = bound.arguments.items()
        passed = dict(arguments)
        if not everything:
            passed = {name: passed[name] for name in names}
        return provider(instance, **passed)

    adapter.__signature__ = signature  # type: ignore[attr-defined]  # read by inspect
    return adapter  # type: ignore[return-value]  # called as contract is


def _parameters(function: Callable[..., Any]) -> list[inspect.Parameter]:
    """``function``'s parameters after ``self``."""
    return list(inspect.signature(function).parameters.values())[1:]


def _shape(function: Callable[..., Any]) -> list[tuple[str, Any, Any]]:
    """What decides how a call binds: each parameter's name, kind and default."""
    return [
        (parameter.name, parameter.kind, _Default(parameter.default))
        for parameter in _parameters(function)
    ]


class _Default:
    """A default compared by value when it can be, by identity otherwise."""

    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _Default):
            return NotImplemented
        if self.value is other.value:
            return True
        try:
            return bool(type(self.value) is type(other.value) and self.value == other.value)
        except Exception:  # a default whose == raises is compared by identity
            return False

    __hash__ = None  # type: ignore[assignment]  # compared only
