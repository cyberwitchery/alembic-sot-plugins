import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from alembic_runner import (
    ApplyOutcome,
    ApplyReport,
    Completed,
    InventoryOutcome,
    Plan,
    PlanOutcome,
    StaleCheck,
    Workspace,
)
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from nautobot.extras.models import GitRepository, SecretsGroup
from nautobot.users.models import ObjectPermission

from nautobot_alembic import actions, jobs, settings
from nautobot_alembic.models import Backend, Flow, Run

User = get_user_model()

INVENTORY = "schema:\n  types: {}\n"
CREATE = {"op": "create", "uid": "u", "type_name": "t"}
PLAN = json.dumps({"schema": {"types": {}}, "ops": [CREATE]})
EMPTY_PLAN = json.dumps({"schema": {"types": {}}, "ops": []})
IMPORTED = '{"schema": {"types": {}}, "objects": [], "from": "import"}'
SETTINGS = {"nautobot_alembic": {"work_root": tempfile.mkdtemp()}}


def completed(argv=("alembic", "plan"), code=0, stderr=""):
    return Completed(list(argv), code, "", stderr, 0.1)


class FakeRunner:
    """stands in for alembic_runner.Runner; tests set what each command returns."""

    def __init__(self, plan=PLAN, stale=False, apply_ok=True):
        self.plan_text, self.stale, self.apply_ok = plan, stale, apply_ok
        self.calls = []
        self.root = Path(tempfile.mkdtemp())

    def check_version(self):
        return "0.10.0"

    def workspace(self, flow):
        return Workspace(self.root, flow.id)

    def import_(self, flow, run, schema_inventory):
        self.calls.append("import")
        path = Workspace(self.root, flow.id).prepare(run) / "imported.json"
        path.write_text(IMPORTED)
        return InventoryOutcome(completed(("alembic", "import")), inventory=path)

    def plan(self, flow, run, inventory):
        self.calls.append("plan")
        return PlanOutcome(completed(), plan=Plan.from_bytes(self.plan_text.encode()))

    def check_stale(self, flow, run, inventory, approved):
        self.calls.append("check_stale")
        return StaleCheck(completed(), stale=self.stale)

    def apply(self, flow, run, plan, expected):
        self.calls.append("apply")
        if not self.apply_ok:
            c = completed(("alembic", "apply"), code=1, stderr="Error: boom")
            return ApplyOutcome(c, error="alembic apply exited 1")
        report = ApplyReport.from_bytes(b'{"applied": [{"uid": "u"}], "provision": {}}')
        return ApplyOutcome(completed(("alembic", "apply")), report=report)


class FakeWorkflow:
    """the parts of an approval workflow the app's hooks read."""

    def __init__(self, responses):
        self.responses = responses


@override_settings(PLUGINS_CONFIG=SETTINGS)
class RunTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create_user(username="requester")
        cls.approver = User.objects.create_user(username="approver")
        cls.bystander = User.objects.create_user(username="bystander")
        permission = ObjectPermission.objects.create(name="runs", actions=["view", "approve"])
        permission.object_types.add(ContentType.objects.get_for_model(Run))
        permission.users.add(cls.requester, cls.approver)
        cls.repository = GitRepository.objects.create(
            name="inventory", slug="inventory", remote_url="file:///tmp/none", branch="main"
        )
        cls.target = Backend.objects.create(
            name="nautobot", kind="nautobot", config={"url": "http://nautobot:8080"}
        )
        cls.flow = Flow.objects.create(
            name="sites",
            git_repository=cls.repository,
            inventory="inventory.yaml",
            target=cls.target,
        )

    def setUp(self):
        self.enqueued = []
        patcher = mock.patch.object(
            actions, "enqueue", lambda name, run, user: self.enqueued.append((name, run.pk))
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        files = mock.patch.object(
            jobs, "flow_files", lambda flow, logger: {"inventory.yaml": INVENTORY}
        )
        files.start()
        self.addCleanup(files.stop)

    def job(self, cls):
        job = cls()
        job.logger = mock.Mock()
        return job

    def plan(self, runner=None, user=None):
        with self.captureOnCommitCallbacks(execute=True):
            run = actions.request_run(self.flow, user or self.requester)
        with mock.patch.object(jobs, "runner", return_value=runner or FakeRunner()):
            self.job(jobs.PlanRun).run(str(run.pk))
        run.refresh_from_db()
        return run

    def approve(self, run, user=None):
        with self.captureOnCommitCallbacks(execute=True):
            return actions.approve(run, user or self.approver)

    def apply(self, run, runner):
        with mock.patch.object(jobs, "runner", return_value=runner):
            self.job(jobs.ApplyRun).run(str(run.pk))
        run.refresh_from_db()
        return run

    def approvals(self, *users):
        return [SimpleNamespace(user=u, user_id=u.pk) for u in users]


class PlanTests(RunTestCase):
    def test_plan_awaits_approval(self):
        run = self.plan()
        self.assertEqual(run.status, "awaiting_approval", run.error)
        self.assertEqual(run.summary["create"], 1)
        self.assertEqual(run.input, {"inventory.yaml": INVENTORY})
        self.assertIn(("PlanRun", run.pk), self.enqueued)

    def test_empty_plan_is_no_changes(self):
        self.assertEqual(self.plan(FakeRunner(plan=EMPTY_PLAN)).status, "no_changes")

    def test_one_run_in_progress_per_flow(self):
        with self.captureOnCommitCallbacks(execute=True):
            actions.request_run(self.flow, self.requester)
        with self.assertRaises(ValidationError):
            actions.request_run(self.flow, self.requester)

    def test_new_plan_supersedes_the_waiting_one(self):
        first = self.plan()
        self.plan()
        first.refresh_from_db()
        self.assertEqual(first.status, "superseded")

    def test_import_is_what_the_run_plans_from(self):
        Flow.objects.filter(pk=self.flow.pk).update(source=self.target)
        self.flow.refresh_from_db()
        runner = FakeRunner()
        run = self.plan(runner)
        self.assertEqual(runner.calls, ["import", "plan"])
        self.assertEqual(run.input, {"inventory.json": IMPORTED})


class FallbackApprovalTests(RunTestCase):
    """no approval workflow applies, so the app's own approve action decides."""

    def test_requester_cannot_approve(self):
        with self.assertRaises(PermissionDenied):
            self.approve(self.plan(), self.requester)

    def test_approve_needs_the_permission(self):
        with self.assertRaises(PermissionDenied):
            self.approve(self.plan(), self.bystander)

    def test_approve_and_apply(self):
        run = self.approve(self.plan())
        self.assertEqual(run.status, "approved")
        self.assertIn(("ApplyRun", run.pk), self.enqueued)
        runner = FakeRunner()
        run = self.apply(run, runner)
        self.assertEqual(run.status, "applied", run.error)
        self.assertEqual(runner.calls, ["check_stale", "apply"])

    def test_stale_plan_writes_nothing(self):
        runner = FakeRunner(stale=True)
        run = self.apply(self.approve(self.plan()), runner)
        self.assertEqual(run.status, "stale")
        self.assertEqual(runner.calls, ["check_stale"])

    def test_changed_plan_is_refused(self):
        run = self.approve(self.plan())
        Run.objects.filter(pk=run.pk).update(plan=EMPTY_PLAN)
        runner = FakeRunner()
        run = self.apply(run, runner)
        self.assertEqual(run.status, "failed")
        self.assertEqual(runner.calls, [])

    def test_failed_apply_resumes_without_stale_check(self):
        run = self.apply(self.approve(self.plan()), FakeRunner(apply_ok=False))
        self.assertEqual(run.status, "apply_failed")
        with self.captureOnCommitCallbacks(execute=True):
            actions.resume(run, self.approver)
        runner = FakeRunner()
        self.assertEqual(self.apply(run, runner).status, "applied")
        self.assertEqual(runner.calls, ["apply"])


class WorkflowTests(RunTestCase):
    """a matching approval workflow decides; the app's hooks move the run."""

    def workflow(self, *users):
        return mock.patch.object(actions, "_approvers", return_value=self.approvals(*users))

    def test_approval_moves_the_run_and_queues_apply(self):
        run = self.plan()
        with self.workflow(self.approver), self.captureOnCommitCallbacks(execute=True):
            run.on_workflow_approved(None)
        run.refresh_from_db()
        self.assertEqual(run.status, "approved")
        self.assertEqual(run.decided_by, self.approver)
        self.assertEqual(run.approved_sha256, run.plan_sha256)
        self.assertIn(("ApplyRun", run.pk), self.enqueued)

    def test_the_requester_alone_does_not_count(self):
        run = self.plan()
        with self.workflow(self.requester):
            run.on_workflow_approved(None)
        run.refresh_from_db()
        self.assertEqual(run.status, "rejected")
        self.assertIn("requester", run.error)

    def test_denied_and_canceled_reject(self):
        for hook in ("on_workflow_denied", "on_workflow_canceled"):
            run = self.plan()
            with self.workflow(self.approver):
                getattr(run, hook)(None)
            run.refresh_from_db()
            self.assertEqual(run.status, "rejected", hook)

    def test_the_app_approve_defers_to_a_workflow(self):
        run = self.plan()
        with mock.patch.object(actions, "workflow_for", return_value=object()):
            with self.assertRaises(ValidationError):
                self.approve(run)

    def test_pending_workflows_are_canceled_by_a_new_plan(self):
        run = self.plan()
        with mock.patch.object(actions, "_cancel_workflows") as cancel:
            self.plan()
        cancel.assert_called_once()
        self.assertEqual(cancel.call_args.args[0].pk, run.pk)


# an alembic plugins directory with two plugins, as an admin would install them.
PLUGINS_DIR = tempfile.mkdtemp(prefix="nautobot-alembic-plugins-")
for _name in ("store", "Lab"):
    Path(PLUGINS_DIR, f"{_name}.yaml").write_text("backend: external\ncommand: /bin/true\n")
PLUGINS = {"nautobot_alembic": {"plugins_dir": PLUGINS_DIR}}


@override_settings(PLUGINS_CONFIG=PLUGINS)
class BackendValidationTests(TestCase):
    def check(self, **fields):
        Backend(name="b", **fields).full_clean()

    def test_valid(self):
        self.check(kind="nautobot", config={"url": "https://n"})
        self.check(kind="store")

    def test_invalid(self):
        group = SecretsGroup.objects.create(name="g")
        for fields in (
            {"kind": "netbox", "config": {"url": "x", "token": "leak"}},
            {"kind": "store", "config": {"setup": {"a": 1}}},
            {"kind": "store", "secrets_group": group},
            {"kind": "nope"},
            {"kind": "external"},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.check(**fields)


@override_settings(PLUGINS_CONFIG=PLUGINS)
class PluginKindTests(TestCase):
    def test_a_plugin_is_a_kind(self):
        kinds = [kind for kind, _ in settings.kind_choices()]
        self.assertEqual(kinds, ["netbox", "nautobot", "infrahub", "peeringdb", "lab", "store"])

    def test_a_plugin_backend_runs_as_the_plugin(self):
        backend = settings.runner_backend(Backend(name="l", kind="lab"))
        self.assertTrue(backend.plugin)
        self.assertEqual(backend.kind, "lab")
