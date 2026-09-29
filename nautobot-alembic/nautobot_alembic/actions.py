"""what a user can do to a run. views, the rest api and the approval workflow
hooks all go through here."""

from alembic_runner import RunStatus, hosting
from alembic_runner.hosting import ActionError, move
from django.db import transaction
from django.utils import timezone

from .models import Run, RunKindChoices
from .settings import setting

S = RunStatus

__all__ = [
    "ActionError",
    "approve",
    "move",
    "refusal",
    "reject",
    "request_run",
    "resume",
    "workflow_approved",
    "workflow_denied",
    "workflow_for",
]


def enqueue(job_class_name, run, user):
    """queue one of the app's jobs for `run`, on the job's own queue."""
    from nautobot.extras.models import Job, JobResult

    job_model = Job.objects.get(module_name="nautobot_alembic.jobs", job_class_name=job_class_name)
    return JobResult.enqueue_job(job_model, user, job_kwargs={"run_id": str(run.pk)})


def refusal(run, user):
    """why `user` may not approve `run` with the app's own action, or None."""
    return hosting.refusal(
        run, user, setting("require_distinct_approver"), setting("plan_ttl_hours")
    )


def request_run(flow, user, kind=RunKindChoices.PLAN):
    """start a plan or drift run. a new plan supersedes the flow's open ones and
    cancels their pending approval workflows."""
    with transaction.atomic():
        run = hosting.create_run(
            Run,
            flow,
            user,
            kind,
            supersede=kind == RunKindChoices.PLAN,
            retire=_cancel_workflows,
        )
        transaction.on_commit(lambda: enqueue("PlanRun", run, user))
    return run


def workflow_for(run):
    """the run's pending approval workflow, if one applies to it."""
    from nautobot.extras.choices import ApprovalWorkflowStateChoices

    return run.associated_approval_workflows.filter(
        current_state=ApprovalWorkflowStateChoices.PENDING
    ).first()


def _cancel_workflows(run):
    from nautobot.extras.choices import ApprovalWorkflowStateChoices

    run.associated_approval_workflows.filter(
        current_state=ApprovalWorkflowStateChoices.PENDING
    ).update(current_state=ApprovalWorkflowStateChoices.CANCELED)


def _approve(run, user):
    """approve a locked run and queue its apply after the commit."""
    approved = hosting.approve(run, user, setting("plan_ttl_hours"))
    if approved:
        transaction.on_commit(lambda: enqueue("ApplyRun", run, user))
    return approved


def approve(run, user):
    """the app's own approval, for a run no approval workflow applies to."""
    with transaction.atomic():
        run = hosting.locked(run)
        if workflow_for(run) is not None:
            raise ActionError("This run is approved through its approval workflow.")
        hosting.check_approver(run, user, setting("require_distinct_approver"))
        approved = _approve(run, user)
    if not approved:
        raise ActionError("This plan is too old to approve. Plan again.")
    return run


def reject(run, user):
    with transaction.atomic():
        run = hosting.locked(run)
        if workflow_for(run) is not None:
            raise ActionError("This run is rejected through its approval workflow.")
        hosting.check_approver(run, user, setting("require_distinct_approver"))
        return hosting.reject(run, user)


def resume(run, user):
    """re-run the approved plan of a failed apply; alembic's journal skips what landed."""
    with transaction.atomic():
        run = hosting.locked(run)
        hosting.check_resumable(run, user)
        transaction.on_commit(lambda: enqueue("ApplyRun", run, user))
    return run


def _approvers(approval_workflow):
    from nautobot.extras.choices import ApprovalWorkflowStateChoices
    from nautobot.extras.models import ApprovalWorkflowStageResponse

    return list(
        ApprovalWorkflowStageResponse.objects.filter(
            approval_workflow_stage__approval_workflow=approval_workflow,
            state=ApprovalWorkflowStateChoices.APPROVED,
        )
        .select_related("user")
        .order_by("last_updated")
    )


def workflow_approved(run, approval_workflow):
    """a workflow's last stage approved the run. the requester alone approving it
    does not count when a distinct approver is required."""
    responses = _approvers(approval_workflow)
    with transaction.atomic():
        run = hosting.locked(run)
        if run.status != S.AWAITING_APPROVAL.value:
            return
        others = [r.user for r in responses if r.user_id != run.requested_by_id]
        if setting("require_distinct_approver") and not others:
            move(
                run,
                S.REJECTED,
                error="Only the requester approved this run; someone else has to.",
                finished_at=timezone.now(),
            )
            return
        _approve(run, (others or [r.user for r in responses] or [None])[-1])


def workflow_denied(run, approval_workflow):
    responses = _approvers(approval_workflow)
    with transaction.atomic():
        run = hosting.locked(run)
        if run.status != S.AWAITING_APPROVAL.value:
            return
        hosting.reject(run, responses[-1].user if responses else None)
