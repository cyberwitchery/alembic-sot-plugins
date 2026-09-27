"""run states and the transitions between them."""

from __future__ import annotations

from enum import Enum


class RunStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    NO_CHANGES = "no_changes"
    DRIFTED = "drifted"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    STALE = "stale"
    APPLY_FAILED = "apply_failed"


S = RunStatus

TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    S.PENDING: frozenset({S.PLANNING, S.FAILED, S.SUPERSEDED}),
    S.PLANNING: frozenset({S.NO_CHANGES, S.DRIFTED, S.AWAITING_APPROVAL, S.FAILED}),
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.SUPERSEDED, S.EXPIRED}),
    S.APPROVED: frozenset({S.APPLYING, S.FAILED}),
    S.APPLYING: frozenset({S.APPLIED, S.STALE, S.APPLY_FAILED}),
    # a failed apply leaves a journal behind: re-running the same plan resumes it.
    # a newer plan for the flow takes that option away.
    S.APPLY_FAILED: frozenset({S.APPLYING, S.SUPERSEDED}),
}

TERMINAL = frozenset(s for s in RunStatus if s not in TRANSITIONS)

# at most one run per flow is in one of these; hosts enforce it in the database.
ACTIVE = frozenset({S.PENDING, S.PLANNING, S.AWAITING_APPROVAL, S.APPROVED, S.APPLYING})


class TransitionError(ValueError):
    pass


def check_transition(current: RunStatus | str, new: RunStatus | str) -> RunStatus:
    """return `new` as a status, or raise when the move from `current` is not allowed."""
    current, new = RunStatus(current), RunStatus(new)
    if new not in TRANSITIONS.get(current, frozenset()):
        raise TransitionError(f"a run cannot move from {current.value} to {new.value}")
    return new
