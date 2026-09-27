"""what a user can do to a run. views and the rest api both go through here."""

from datetime import timedelta

from alembic_runner import RunStatus, TransitionError, check_transition
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Run, RunKindChoices
from .settings import setting

S = RunStatus
QUEUE = "netbox_alembic.alembic"


class ActionError(ValidationError):
    pass


def move(run, new, **fields):
    """move a locked run to `new`, setting `fields`, or raise ActionError."""
    try:
        check_transition(run.status, new)
    except TransitionError as e:
        raise ActionError(str(e)) from e
    run.status = RunStatus(new).value
    for name, value in fields.items():
        setattr(run, name, value)
    run.save()
    return run


def _locked(run):
    return Run.objects.select_for_update().get(pk=run.pk)


def expired(run):
    ttl = setting("plan_ttl_hours")
    return bool(ttl and run.planned_at and timezone.now() > run.planned_at + timedelta(hours=ttl))


def request_run(flow, user, kind=RunKindChoices.PLAN):
    """start a plan or drift run. a new plan supersedes the flow's open ones."""
    from .jobs import PlanRunJob

    with transaction.atomic():
        if kind == RunKindChoices.PLAN:
            open_runs = Run.objects.select_for_update().filter(
                flow=flow, status__in=[S.AWAITING_APPROVAL.value, S.APPLY_FAILED.value]
            )
            for run in open_runs:
                move(run, S.SUPERSEDED, finished_at=timezone.now())
        try:
            with transaction.atomic():
                run = Run.objects.create(flow=flow, kind=kind, requested_by=user)
        except IntegrityError:
            raise ActionError(_("this flow already has a run in progress")) from None
        transaction.on_commit(lambda: PlanRunJob.enqueue(instance=run, user=user, queue_name=QUEUE))
    return run


def _check_approver(run, user):
    if not user.has_perm("netbox_alembic.approve_run", run):
        raise PermissionDenied(_("you may not approve or reject this run"))
    if setting("require_distinct_approver") and run.requested_by_id == user.pk:
        raise PermissionDenied(_("a run is approved by someone other than its requester"))


def approve(run, user):
    from .jobs import ApplyRunJob

    with transaction.atomic():
        run = _locked(run)
        _check_approver(run, user)
        if run.status == S.AWAITING_APPROVAL.value and expired(run):
            move(run, S.EXPIRED, finished_at=timezone.now())
            late = True
        else:
            late = False
    if late:
        raise ActionError(_("this plan is too old to approve; plan again"))

    with transaction.atomic():
        run = _locked(run)
        _check_approver(run, user)
        move(
            run,
            S.APPROVED,
            decided_by=user,
            decided_at=timezone.now(),
            approved_sha256=run.plan_sha256,
        )
        transaction.on_commit(
            lambda: ApplyRunJob.enqueue(instance=run, user=user, queue_name=QUEUE)
        )
    return run


def reject(run, user):
    with transaction.atomic():
        run = _locked(run)
        _check_approver(run, user)
        return move(
            run,
            S.REJECTED,
            decided_by=user,
            decided_at=timezone.now(),
            finished_at=timezone.now(),
        )


def resume(run, user):
    """re-run the approved plan of a failed apply; alembic's journal skips what landed."""
    from .jobs import ApplyRunJob

    with transaction.atomic():
        run = _locked(run)
        _check_approver(run, user)
        if run.status != S.APPLY_FAILED.value:
            raise ActionError(_("only a failed apply can be resumed"))
        transaction.on_commit(
            lambda: ApplyRunJob.enqueue(instance=run, user=user, queue_name=QUEUE)
        )
    return run
