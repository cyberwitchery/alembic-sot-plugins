"""background jobs that run alembic. they run on the plugin's own queue."""

from alembic_runner import RunnerError, RunStatus, WorkspaceError, hosting
from alembic_runner.pipeline import apply_run, plan_run, snapshot
from core.models import DataFile
from django.utils import timezone
from netbox.jobs import JobRunner

from .models import Run, RunKindChoices
from .settings import SettingsError, runner, runner_flow

S = RunStatus


def flow_files(flow, logger):
    """the flow's files as the data source holds them after a sync, keyed by
    their path under the flow's root."""
    source = flow.data_source
    logger.info(f"syncing data source {source}")
    source.sync()
    prefix = flow.root_prefix
    items = (
        (datafile.path[len(prefix) :], bytes(datafile.data))
        for datafile in DataFile.objects.filter(source=source, path__startswith=prefix)
    )
    return snapshot(items, flow.inventory, f"data source {source}", logger)


class RunJob(JobRunner):
    """load the run and turn any failure into a run status."""

    def run(self, *args, **kwargs):
        run = Run.objects.select_related(
            "flow", "flow__target", "flow__source", "flow__data_source"
        ).get(pk=self.job.object_id)
        hosting.run_guarded(
            run, self.execute, self.logger, (RunnerError, SettingsError, WorkspaceError)
        )


class PlanRunJob(RunJob):
    class Meta:
        name = "alembic plan"

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
            f"data source {run.flow.data_source}",
            run.kind == RunKindChoices.DRIFT,
            self.logger,
        )
        hosting.settle(run, outcome, planned_at=timezone.now())


class ApplyRunJob(RunJob):
    class Meta:
        name = "alembic apply"

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
