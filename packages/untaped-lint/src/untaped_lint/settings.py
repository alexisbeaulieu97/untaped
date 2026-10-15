"""The lint plugin's profile settings: ``lint.ignore``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from untaped.sdk import UsageError


class LintSettings(BaseModel):
    """What every ``untaped lint`` run in the profile leaves out."""

    model_config = ConfigDict(frozen=True)

    ignore: list[str] = Field(
        default_factory=list,
        description=(
            "Findings to leave out of every run, each <rule>[:<target-glob>], as "
            "lint.error-case or lint.print:acme (a plugin's findings target its name)."
        ),
    )

    @field_validator("ignore")
    @classmethod
    def _spelled(cls, value: list[str]) -> list[str]:
        # Read at startup with the spec: the selection modules load only for a value.
        from untaped_lint.domain.selection import parse_suppression  # noqa: PLC0415

        for each in value:
            try:
                parse_suppression(each, option="lint.ignore")
            except UsageError as exc:
                raise ValueError(str(exc)) from None
        return value
