"""Public hook authoring contract for recipe pack hook projects."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from untaped.capabilities.recipe.application.ports import HookHelpers as HookHelpers
from untaped.capabilities.recipe.domain.hook_project import HOOK_API_VERSION as HOOK_API_VERSION


class YamlIndentOptions(TypedDict, total=False):
    """Indentation options passed to ``helpers.dump_yaml``."""

    mapping: int
    sequence: int
    offset: int


class YamlDumpOptions(TypedDict, total=False):
    """YAML dump formatting options accepted by external hook helpers."""

    width: int
    preserve_quotes: bool
    indent: YamlIndentOptions
    block_seq_indent: int
    explicit_start: bool
    explicit_end: bool


__all__ = ["HOOK_API_VERSION", "HookHelpers", "YamlDumpOptions", "YamlIndentOptions"]


if TYPE_CHECKING:

    def _plain_mapping_options_are_supported(
        helpers: HookHelpers,
        options: dict[str, object],
    ) -> str:
        return helpers.dump_yaml({}, options=options)

    def _typed_options_are_supported(
        helpers: HookHelpers,
        options: YamlDumpOptions,
    ) -> str:
        return helpers.dump_yaml({}, options=options)
