"""run bookkeeping the host apps share: moves, locks, approval checks and the job
wrapper. it needs django, so only the host apps import it; the rest of the core
stays stdlib-only.

a host's run model carries the fields both apps define: `flow`, `kind`,
`status`, `requested_by`, `decided_by`, `decided_at`, `planned_at`,
`apply_started_at`, `finished_at`, `plan`, `plan_sha256`, `approved_sha256`,
`error`, `output`."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .pipeline import Outcome, RunFailed, approved_plan
from .plan import Plan
from .status import RunStatus, TransitionError, check_transition, failure_status

S = RunStatus

# a new plan of a flow takes these runs' place.
SUPERSEDABLE = (S.AWAITING_APPROVAL.value, S.APPLY_FAILED.value)


class ActionError(ValidationError):
    """a run cannot do what was asked of it in its current state."""


def _save(run) -> None:
    # nautobot validates on save through `validated_save`; netbox has only `save`.
    getattr(run, "validated_save", run.save)()


def move(run, new: RunStatus | str, **fields: Any):
    """move a locked run to `new`, setting `fields`, or raise ActionError."""
    try:
        check_transition(run.status, new)
    except TransitionError as e:
        raise ActionError(str(e)) from e
    run.status = RunStatus(new).value
    for name, value in fields.items():
        setattr(run, name, value)
    _save(run)
    return run


def locked(run):
    return type(run).objects.select_for_update().get(pk=run.pk)


def expired(run, ttl_hours: int | None) -> bool:
    return bool(
        ttl_hours
        and run.planned_at
        and timezone.now() > run.planned_at + timedelta(hours=ttl_hours)
    )


def create_run(model, flow, user, kind: str, supersede: bool, retire: Callable = lambda run: None):
    """create a pending run of `flow`. a plan (`supersede`) takes the place of the
    flow's waiting runs, each passed to `retire` first. call inside a transaction."""
    if supersede:
        for run in model.objects.select_for_update().filter(flow=flow, status__in=SUPERSEDABLE):
            retire(run)
            move(run, S.SUPERSEDED, finished_at=timezone.now())
    try:
        with transaction.atomic():
            return model.objects.create(flow=flow, kind=kind, requested_by=user)
    except IntegrityError:
        raise ActionError("This flow already has a run in progress.") from None


def _approve_perm(run) -> str:
    return f"{run._meta.app_label}.approve_run"


def refusal(run, user, distinct: bool, ttl_hours: int | None = None) -> str | None:
    """why `user` may not approve or reject `run`, or None if they may."""
    if not user.has_perm(_approve_perm(run), run):
        return "You may not approve or reject this run."
    if distinct and run.requested_by_id == user.pk:
        return "Someone other than the requester approves this run."
    if expired(run, ttl_hours):
        return "This plan is too old to approve. Plan again."
    return None


def check_approver(run, user, distinct: bool) -> None:
    reason = refusal(run, user, distinct)
    if reason:
        raise PermissionDenied(reason)


def approve(run, user, ttl_hours: int | None) -> bool:
    """move a locked, waiting run to approved, or to expired when its plan is too
    old. returns whether it was approved; the caller queues the apply."""
    if expired(run, ttl_hours):
        move(run, S.EXPIRED, finished_at=timezone.now())
        return False
    move(
        run,
        S.APPROVED,
        decided_by=user,
        decided_at=timezone.now(),
        approved_sha256=run.plan_sha256,
    )
    return True


def reject(run, user):
    now = timezone.now()
    return move(run, S.REJECTED, decided_by=user, decided_at=now, finished_at=now)


def check_resumable(run, user) -> None:
    if not user.has_perm(_approve_perm(run), run):
        raise PermissionDenied("You may not resume this run.")
    if run.status != S.APPLY_FAILED.value:
        raise ActionError("Only a failed apply can be resumed.")


# jobs


def transition(run, new: RunStatus | str, **fields: Any) -> None:
    with transaction.atomic():
        move(locked(run), new, **fields)
    run.refresh_from_db()


def settle(run, outcome: Outcome, **fields: Any) -> None:
    """store what a pipeline step decided on the run."""
    if outcome.finished:
        fields["finished_at"] = timezone.now()
    transition(run, outcome.status, **outcome.fields, **fields)


def fail(run, message: str, output: str = "") -> None:
    """record a failure on the run, in the status its phase fails into."""
    fields: dict[str, Any] = {"error": message}
    if output:
        fields["output"] = output
    with transaction.atomic():
        run = locked(run)
        new = failure_status(run.status)
        if new is S.FAILED:
            fields["finished_at"] = timezone.now()
        if new is not None:
            move(run, new, **fields)


def run_guarded(run, execute: Callable, logger, known: Iterable[type[Exception]] = ()) -> None:
    """run a job's body. a failure it expects becomes the run's status; anything
    else does too, and is raised again so the host marks its job as errored."""
    try:
        execute(run)
    except RunFailed as e:
        logger.error(str(e))
        fail(run, str(e), e.output)
    except ActionError as e:
        # the run is not in a state this job can act on; leave it as it is.
        logger.error(" ".join(e.messages))
    except tuple(known) as e:
        logger.error(str(e))
        fail(run, str(e))
    except Exception as e:
        fail(run, f"{type(e).__name__}: {e}")
        raise


def start_apply(run) -> tuple[Plan, bool]:
    """take the run into applying: one apply per target at a time, from an
    approved run or a failed apply, and only with the plan that was approved.
    returns the plan and whether this resumes a failed apply."""
    model = type(run)
    with transaction.atomic():
        current = locked(run)
        busy = (
            model.objects.filter(flow__target=run.flow.target, status=S.APPLYING.value)
            .exclude(pk=run.pk)
            .select_for_update()
        )
        if busy.exists():
            raise RunFailed(f"Another run is applying to {run.flow.target}; try again later.")
        if current.status not in (S.APPROVED.value, S.APPLY_FAILED.value):
            raise ActionError(f"a {current.status} run is not applied")
        resuming = current.status == S.APPLY_FAILED.value
        # checked before the move, so a plan that fails it never becomes resumable.
        plan = approved_plan(current.plan, current.approved_sha256)
        move(current, S.APPLYING, apply_started_at=current.apply_started_at or timezone.now())
    run.refresh_from_db()
    return plan, resuming
