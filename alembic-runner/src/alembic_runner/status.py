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


TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.PLANNING, RunStatus.FAILED, RunStatus.SUPERSEDED}),
    # an edit to the flow supersedes a run before it applies; see hosting.EDIT_SUPERSEDES.
    RunStatus.PLANNING: frozenset(
        {
            RunStatus.NO_CHANGES,
            RunStatus.DRIFTED,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.FAILED,
            RunStatus.SUPERSEDED,
        }
    ),
    RunStatus.AWAITING_APPROVAL: frozenset(
        {RunStatus.APPROVED, RunStatus.REJECTED, RunStatus.SUPERSEDED, RunStatus.EXPIRED}
    ),
    RunStatus.APPROVED: frozenset({RunStatus.APPLYING, RunStatus.FAILED, RunStatus.SUPERSEDED}),
    RunStatus.APPLYING: frozenset({RunStatus.APPLIED, RunStatus.STALE, RunStatus.APPLY_FAILED}),
    # a failed apply leaves a journal behind: re-running the same plan resumes it.
    # a newer plan for the flow takes that option away.
    RunStatus.APPLY_FAILED: frozenset({RunStatus.APPLYING, RunStatus.SUPERSEDED}),
}

# at most one run per flow is in one of these; hosts enforce it in the database.
ACTIVE = frozenset(
    {
        RunStatus.PENDING,
        RunStatus.PLANNING,
        RunStatus.AWAITING_APPROVAL,
        RunStatus.APPROVED,
        RunStatus.APPLYING,
    }
)

# an apply job starts from these: an approval, or a failed apply it resumes.
STARTABLE = frozenset({RunStatus.APPROVED, RunStatus.APPLY_FAILED})


def failure_status(current: RunStatus | str) -> RunStatus | None:
    """where a run goes when its job fails: a failed plan is over, a failed apply
    can be resumed, and a run no job was working on stays as it is."""
    current = RunStatus(current)
    if current in (RunStatus.PENDING, RunStatus.PLANNING, RunStatus.APPROVED):
        return RunStatus.FAILED
    if current is RunStatus.APPLYING:
        return RunStatus.APPLY_FAILED
    return None


class TransitionError(ValueError):
    pass


def check_transition(current: RunStatus | str, new: RunStatus | str) -> RunStatus:
    """return `new` as a status, or raise when the move from `current` is not allowed."""
    current, new = RunStatus(current), RunStatus(new)
    if new not in TRANSITIONS.get(current, frozenset()):
        raise TransitionError(f"a run cannot move from {current.value} to {new.value}")
    return new
