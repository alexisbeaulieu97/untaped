from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from untaped_core.validators import ValidationOutcome


@dataclass(slots=True)
class ServiceResult:
    """Represents the outcome of a service operation."""

    outcome: ValidationOutcome
    response: dict[str, Any] | None = None
    dry_run: bool = False

    @property
    def is_successful(self) -> bool:
        return self.outcome.validation.is_valid and self.response is not None
