"""background jobs that run alembic. they run on the plugin's own queue."""

import hashlib
import json

from alembic_runner import Plan, RunnerError, RunStatus, WorkspaceError
from core.models import DataFile
from django.db import transaction
from django.utils import timezone
from netbox.jobs import JobRunner

from .actions import ActionError, move
from .models import Run, RunKindChoices
from .settings import SettingsError, runner, runner_flow

S = RunStatus

SNAPSHOT_LIMIT = 20 << 20


class RunFailed(Exception):
    def __init__(self, message, output=""):
        super().__init__(message)
        self.output = output


def failed(outcome, output):
    return RunFailed(f"{outcome.error}\n{outcome.completed.stderr}".strip(), output)


def _output(*completed, before=""):
    """alembic's output, one block per command, after what earlier jobs recorded."""
    blocks = [before.rstrip()] if before else []
    for c in completed:
        body = f"{c.stdout}{c.stderr}".rstrip()
        blocks.append(f"$ alembic {c.argv[1]}\n{body}" if body else f"$ alembic {c.argv[1]}")
    return "\n\n".join(blocks)


def snapshot(flow, logger):
    """the flow's files as the data source holds them after a sync, keyed by
    their path under the flow's root."""
    source = flow.data_source
    logger.info(f"syncing data source {source}")
    source.sync()
    prefix = flow.root_prefix
    files, total = {}, 0
    for datafile in DataFile.objects.filter(source=source, path__startswith=prefix):
        data = bytes(datafile.data)
        total += len(data)
        if total > SNAPSHOT_LIMIT:
            raise RunFailed(f"the files under {prefix or 'the root'} exceed {SNAPSHOT_LIMIT} bytes")
        try:
            files[datafile.path[len(prefix) :]] = data.decode("utf-8")
        except UnicodeDecodeError:
            logger.info(f"skipping {datafile.path}: not utf-8 text")
    if flow.inventory not in files:
        raise RunFailed(f"{prefix}{flow.inventory} is not in data source {source}")
    return files


def input_sha256(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


class RunJob(JobRunner):
    """shared plumbing: load the run, turn any failure into a run status."""

    failure = S.FAILED

    def run(self, *args, **kwargs):
        run = Run.objects.select_related("flow", "flow__target", "flow__data_source").get(
            pk=self.job.object_id
        )
        try:
            self.execute(run)
        except RunFailed as e:
            self.fail(run, str(e), e.output)
        except ActionError as e:
            # the run is not in a state this job can act on; leave it as it is.
            self.logger.error(" ".join(e.messages))
        except (RunnerError, SettingsError, WorkspaceError) as e:
            self.fail(run, str(e))
        except Exception as e:
            self.fail(run, f"{type(e).__name__}: {e}")
            raise

    def fail(self, run, message, output=""):
        self.logger.error(message)
        fields = {"error": message}
        if output:
            fields["output"] = output
        with transaction.atomic():
            run = Run.objects.select_for_update().get(pk=run.pk)
            if run.status in (S.PLANNING.value, S.PENDING.value, S.APPROVED.value):
                move(run, S.FAILED, finished_at=timezone.now(), **fields)
            elif run.status == S.APPLYING.value:
                move(run, S.APPLY_FAILED, **fields)

    def transition(self, run, new, **fields):
        with transaction.atomic():
            locked = Run.objects.select_for_update().get(pk=run.pk)
            move(locked, new, **fields)
        run.refresh_from_db()


class PlanRunJob(RunJob):
    class Meta:
        name = "alembic plan"

    def execute(self, run):
        self.transition(run, S.PLANNING)
        r = runner()
        version = r.check_version()
        Run.objects.filter(pk=run.pk).update(alembic_version=version)
        flow = runner_flow(run.flow)
        files = snapshot(run.flow, self.logger)
        inventory = r.workspace(flow).write_input(str(run.pk), files, run.flow.inventory)
        now = timezone.now()
        common = {"input": files, "input_sha256": input_sha256(files), "planned_at": now}

        if run.kind == RunKindChoices.DRIFT:
            outcome = r.drift(flow, str(run.pk), inventory)
            output = _output(outcome.completed)
            if outcome.error:
                raise failed(outcome, output)
            report = outcome.report
            self.transition(
                run,
                S.DRIFTED if report.has_drift else S.NO_CHANGES,
                drift_report=report.doc,
                summary=report.counts(),
                output=output,
                finished_at=now,
                **common,
            )
            self.logger.info(f"drift: {report.counts()}")
            return

        outcome = r.plan(flow, str(run.pk), inventory)
        output = _output(outcome.completed)
        if outcome.error:
            raise failed(outcome, output)
        plan = outcome.plan
        summary = {**plan.summary(), "schema_preview": plan.schema_preview}
        fields = {
            "plan": plan.raw.decode("utf-8"),
            "plan_sha256": plan.sha256,
            "summary": summary,
            "output": output,
            **common,
        }
        if plan.is_empty:
            self.transition(run, S.NO_CHANGES, finished_at=now, **fields)
        else:
            self.transition(run, S.AWAITING_APPROVAL, **fields)
        self.logger.info(f"plan: {plan.summary()}")


class ApplyRunJob(RunJob):
    class Meta:
        name = "alembic apply"

    def execute(self, run):
        resuming = run.status == S.APPLY_FAILED.value
        with transaction.atomic():
            # one apply per target at a time, whatever flow it comes from.
            locked = Run.objects.select_for_update().get(pk=run.pk)
            busy = (
                Run.objects.filter(flow__target=run.flow.target, status=S.APPLYING.value)
                .exclude(pk=run.pk)
                .select_for_update()
            )
            if busy.exists():
                raise RunFailed(f"another run is applying to {run.flow.target}; try again later")
            if locked.status not in (S.APPROVED.value, S.APPLY_FAILED.value):
                raise ActionError(f"a {locked.status} run is not applied")
            # checked before the move, so a plan that fails it never becomes resumable.
            plan = Plan.from_bytes(locked.plan.encode("utf-8"))
            if not locked.approved_sha256 or plan.sha256 != locked.approved_sha256:
                raise RunFailed("the stored plan is not the one that was approved")
            move(locked, S.APPLYING, apply_started_at=locked.apply_started_at or timezone.now())
        run.refresh_from_db()

        r = runner()
        r.check_version()
        flow = runner_flow(run.flow)
        inventory = r.workspace(flow).write_input(str(run.pk), run.input, run.flow.inventory)

        outputs = []
        if not resuming:
            # a resumed apply re-runs its plan on top of what already landed, so a
            # re-plan would differ by construction. the journal carries it instead.
            check = r.check_stale(flow, str(run.pk), inventory, plan)
            outputs.append(check.completed)
            if check.error:
                raise failed(check, _output(*outputs, before=run.output))
            verdict = "changed" if check.stale else "is unchanged"
            self.logger.info(f"stale check: the target {verdict}")
            if check.stale:
                self.logger.warning("the target changed since the plan was made")
                self.transition(
                    run,
                    S.STALE,
                    output=_output(*outputs, before=run.output),
                    error="the target changed since this plan was made; plan again",
                    finished_at=timezone.now(),
                )
                return

        outcome = r.apply(flow, str(run.pk), plan, run.approved_sha256)
        outputs.append(outcome.completed)
        if outcome.error:
            raise failed(outcome, _output(*outputs, before=run.output))
        self.transition(
            run,
            S.APPLIED,
            apply_report=outcome.report.doc,
            output=_output(*outputs, before=run.output),
            error="",
            finished_at=timezone.now(),
        )
        self.logger.info(f"applied {outcome.report.applied_count} operations")
