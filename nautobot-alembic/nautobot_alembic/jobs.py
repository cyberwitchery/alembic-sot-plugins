"""background jobs that run alembic. they run on the app's own queue."""

import os
from pathlib import Path

from alembic_runner import RunnerError, RunStatus, WorkspaceError, hosting
from alembic_runner.pipeline import apply_run, plan_run, snapshot
from django.utils import timezone
from nautobot.apps.jobs import Job, StringVar, register_jobs

from .models import Run, RunKindChoices
from .settings import SettingsError, runner, runner_flow

S = RunStatus


def flow_files(flow, logger):
    """the flow's files as the repository holds them after a pull, keyed by their
    path under the flow's root."""
    from nautobot.extras.datasources.git import ensure_git_repository

    repository = flow.git_repository
    logger.info(f"pulling git repository {repository}")
    ensure_git_repository(repository, logger=logger)
    base = Path(repository.filesystem_path) / flow.root

    def items():
        for directory, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in names:
                path = Path(directory) / name
                yield path.relative_to(base).as_posix(), path.read_bytes()

    return snapshot(items(), flow.inventory, f"git repository {repository}", logger)


class RunJob(Job):
    """load the run and turn any failure into a run status."""

    run_id = StringVar(description="The run this job acts on.")

    class Meta:
        hidden = True
        has_sensitive_variables = False
        task_queues = ["alembic"]

    def run(self, run_id):
        run = Run.objects.select_related(
            "flow", "flow__target", "flow__source", "flow__git_repository"
        ).get(pk=run_id)
        hosting.run_guarded(
            run, self.execute, self.logger, (RunnerError, SettingsError, WorkspaceError)
        )


class PlanRun(RunJob):
    class Meta(RunJob.Meta):
        name = "Alembic plan"
        description = "Plan a flow's inventory against its target."

    def execute(self, run):
        hosting.transition(run, S.PLANNING)
        r = runner()
        version = r.check_version()
        Run.objects.filter(pk=run.pk).update(alembic_version=version)
        outcome = plan_run(
            r,
            runner_flow(run.flow),
            str(run.pk),
            flow_files(run.flow, self.logger),
            run.flow.inventory,
            run.flow.map_spec,
            f"git repository {run.flow.git_repository}",
            run.kind == RunKindChoices.DRIFT,
            self.logger,
        )
        hosting.settle(run, outcome, planned_at=timezone.now())
        if run.status == S.AWAITING_APPROVAL.value:
            # a matching approval workflow takes the decision; without one the
            # app's own approve action does.
            workflow = run.begin_approval_workflow()
            if workflow is not None:
                self.logger.info(f"approval workflow {workflow} started")


class ApplyRun(RunJob):
    class Meta(RunJob.Meta):
        name = "Alembic apply"
        description = "Apply a run's approved plan to its target."

    def execute(self, run):
        plan, resuming = hosting.start_apply(run)
        r = runner()
        r.check_version()
        outcome = apply_run(
            r,
            runner_flow(run.flow),
            str(run.pk),
            run.input,
            run.input_entry or run.flow.inventory,
            plan,
            run.approved_sha256,
            resuming,
            run.output,
            self.logger,
        )
        hosting.settle(run, outcome)


register_jobs(PlanRun, ApplyRun)
