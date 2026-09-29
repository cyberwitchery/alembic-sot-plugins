"""what a user can do to a run. views and the rest api both go through here."""

from alembic_runner import hosting
from alembic_runner.hosting import ActionError, move
from django.db import transaction

from .models import Run, RunKindChoices
from .settings import setting

QUEUE = "netbox_alembic.alembic"

__all__ = ["ActionError", "approve", "move", "refusal", "reject", "request_run", "resume"]


def refusal(run, user):
    """why `user` may not approve `run`, or None."""
    return hosting.refusal(
        run, user, setting("require_distinct_approver"), setting("plan_ttl_hours")
    )


def request_run(flow, user, kind=RunKindChoices.PLAN):
    """start a plan or drift run. a new plan supersedes the flow's open ones."""
    from .jobs import PlanRunJob

    with transaction.atomic():
        run = hosting.create_run(Run, flow, user, kind, supersede=kind == RunKindChoices.PLAN)
        transaction.on_commit(lambda: PlanRunJob.enqueue(instance=run, user=user, queue_name=QUEUE))
    return run


def approve(run, user):
    from .jobs import ApplyRunJob

    with transaction.atomic():
        run = hosting.locked(run)
        hosting.check_approver(run, user, setting("require_distinct_approver"))
        approved = hosting.approve(run, user, setting("plan_ttl_hours"))
        if approved:
            transaction.on_commit(
                lambda: ApplyRunJob.enqueue(instance=run, user=user, queue_name=QUEUE)
            )
    if not approved:
        raise ActionError("This plan is too old to approve. Plan again.")
    return run


def reject(run, user):
    with transaction.atomic():
        run = hosting.locked(run)
        hosting.check_approver(run, user, setting("require_distinct_approver"))
        return hosting.reject(run, user)


def resume(run, user):
    """re-run the approved plan of a failed apply; alembic's journal skips what landed."""
    from .jobs import ApplyRunJob

    with transaction.atomic():
        run = hosting.locked(run)
        hosting.check_resumable(run, user)
        transaction.on_commit(
            lambda: ApplyRunJob.enqueue(instance=run, user=user, queue_name=QUEUE)
        )
    return run
