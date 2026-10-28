"""Shared types for invariant modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    rule: str  # "I1".."I10"
    message: str
    where: tuple  # key path into the file, for the line locator
    runtime: bool = False  # depends on runtime facts: warning at L1-L2, error at L3-L4
    force: Severity | None = None  # overrides the level rule (advisories)

    def severity(self, runtime_is_error: bool) -> Severity:
        if self.force:
            return self.force
        if self.runtime and not runtime_is_error:
            return "warning"
        return "error"
