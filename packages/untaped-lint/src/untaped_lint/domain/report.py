"""What a run found, counted: findings by severity and the failure rows."""

from __future__ import annotations

from dataclasses import dataclass, field

from untaped.sdk import Failure, plural
from untaped_lint.records import Finding


@dataclass
class Report:
    """The findings a run reports and the failures that kept rules from running."""

    findings: list[Finding] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """Things the user should know that are neither (an unknown id, a kind nobody reads)."""

    @property
    def errors(self) -> int:
        return sum(finding.severity == "error" for finding in self.findings)

    def predicate_hit(self, *, strict: bool) -> bool:
        """Whether the run exits 3: an error finding, or any finding under ``strict``."""
        return bool(self.findings) if strict else self.errors > 0

    def summary(self) -> str:
        warnings = len(self.findings) - self.errors
        parts = [plural(self.errors, "error"), plural(warnings, "warning")]
        if self.failures:
            parts.append(plural(len(self.failures), "failure"))
        return f"lint: {', '.join(parts)}"
