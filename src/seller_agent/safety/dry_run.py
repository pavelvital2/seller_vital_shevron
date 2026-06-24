from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DryRunDecision:
    is_apply_allowed: bool
    reason: str


def read_only_decision() -> DryRunDecision:
    return DryRunDecision(is_apply_allowed=False, reason="read-only task")

