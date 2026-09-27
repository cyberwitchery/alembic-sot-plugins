import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from alembic_runner import ApplyOutcome, ApplyReport, Completed, Plan, PlanOutcome, StaleCheck
from core.models import DataFile, DataSource
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from users.models import ObjectPermission, User

from netbox_alembic import AlembicConfig, actions
from netbox_alembic.jobs import ApplyRunJob, PlanRunJob
from netbox_alembic.models import Backend, Flow, Run

PLUGIN_SETTINGS = {
    "netbox_alembic": {
        **AlembicConfig.default_settings,
        "work_root": "/tmp/netbox-alembic-tests",
        "self_url": "http://netbox:8080",
        "self_credential": "self",
        "credentials": {"self": {"token": "TEST_SELF_TOKEN"}, "other": {"token": "TEST_OTHER"}},
        "external_adapters": {"store": "/usr/local/bin/alembic-adapter-store"},
    }
}

INVENTORY = "schema:\n  types: {}\n"
CREATE = {"op": "create", "uid": "u", "type_name": "t"}
PLAN = json.dumps({"schema": {"types": {}}, "ops": [CREATE]})
EMPTY_PLAN = json.dumps({"schema": {"types": {}}, "ops": []})


def completed(argv=("alembic", "plan"), code=0, stdout="", stderr=""):
    return Completed(list(argv), code, stdout, stderr, 0.1)


class FakeRunner:
    """stands in for alembic_runner.Runner; tests set what each command returns."""

    def __init__(self, plan=PLAN, stale=False, apply_ok=True):
        self.plan_text, self.stale, self.apply_ok = plan, stale, apply_ok
        self.calls = []

    def check_version(self):
        return "0.9.0"

    def workspace(self, flow):
        return SimpleNamespace(write_input=lambda run, files, entry: f"/tmp/{run}/{entry}")

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


def job_for(run):
    return SimpleNamespace(object_id=run.pk, log=lambda record: None)


@override_settings(PLUGINS_CONFIG=PLUGIN_SETTINGS)
class RunTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create_user(username="requester")
        cls.approver = User.objects.create_user(username="approver")
        cls.bystander = User.objects.create_user(username="bystander")
        permission = ObjectPermission.objects.create(
            name="approve runs", actions=["view", "approve"]
        )
        permission.object_types.add(ContentType.objects.get_for_model(Run))
        permission.users.add(cls.requester, cls.approver)
        cls.source = DataSource.objects.create(
            name="inventory", type="local", source_url="file:///tmp/inventory"
        )
        for path, content in {"inv/inventory.yaml": INVENTORY, "other.yaml": "x"}.items():
            DataFile.objects.create(
                source=cls.source,
                path=path,
                data=content.encode(),
                size=len(content),
                hash="0" * 64,
                last_updated=timezone.now(),
            )
        cls.target = Backend.objects.create(name="self", kind="netbox", is_self=True)
        cls.flow = Flow.objects.create(
            name="sites",
            data_source=cls.source,
            root="inv",
            inventory="inventory.yaml",
            target=cls.target,
        )

    def setUp(self):
        patcher = mock.patch.dict("os.environ", {"TEST_SELF_TOKEN": "t", "TEST_OTHER": "o"})
        patcher.start()
        self.addCleanup(patcher.stop)
        sync = mock.patch.object(DataSource, "sync")
        sync.start()
        self.addCleanup(sync.stop)
        self.enqueued = []
        for job in (PlanRunJob, ApplyRunJob):
            p = mock.patch.object(
                job,
                "enqueue",
                lambda *a, _j=job, **k: self.enqueued.append((_j.__name__, k["instance"].pk)),
            )
            p.start()
            self.addCleanup(p.stop)

    def request(self, user=None):
        with self.captureOnCommitCallbacks(execute=True):
            return actions.request_run(self.flow, user or self.requester)

    def plan(self, runner=None, user=None):
        run = self.request(user)
        with mock.patch("netbox_alembic.jobs.runner", return_value=runner or FakeRunner()):
            PlanRunJob(job_for(run)).run()
        run.refresh_from_db()
        return run

    def approve(self, run, user=None):
        with self.captureOnCommitCallbacks(execute=True):
            return actions.approve(run, user or self.approver)

    def apply(self, run, runner):
        with mock.patch("netbox_alembic.jobs.runner", return_value=runner):
            ApplyRunJob(job_for(run)).run()
        run.refresh_from_db()
        return run


class PlanTests(RunTestCase):
    def test_plan_awaits_approval(self):
        run = self.plan()
        self.assertEqual(run.status, "awaiting_approval")
        self.assertEqual(run.summary["create"], 1)
        self.assertEqual(run.plan_sha256, Plan.from_bytes(PLAN.encode()).sha256)
        self.assertEqual(run.alembic_version, "0.9.0")
        self.assertIn(("PlanRunJob", run.pk), self.enqueued)

    def test_input_is_snapshotted_under_the_root(self):
        run = self.plan()
        self.assertEqual(run.input, {"inventory.yaml": INVENTORY})
        self.assertEqual(len(run.input_sha256), 64)

    def test_empty_plan_is_no_changes(self):
        run = self.plan(FakeRunner(plan=EMPTY_PLAN))
        self.assertEqual(run.status, "no_changes")
        self.assertIsNotNone(run.finished_at)

    def test_missing_inventory_fails_the_run(self):
        Flow.objects.filter(pk=self.flow.pk).update(inventory="missing.yaml")
        self.flow.refresh_from_db()
        run = self.plan()
        self.assertEqual(run.status, "failed")
        self.assertIn("missing.yaml", run.error)

    def test_missing_credential_fails_the_run(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            run = self.plan()
        self.assertEqual(run.status, "failed")
        self.assertIn("TEST_SELF_TOKEN", run.error)

    def test_one_run_in_progress_per_flow(self):
        self.request()
        with self.assertRaises(ValidationError):
            self.request()

    def test_new_plan_supersedes_the_waiting_one(self):
        first = self.plan()
        second = self.plan()
        first.refresh_from_db()
        self.assertEqual(first.status, "superseded")
        self.assertEqual(second.status, "awaiting_approval")


class ApprovalTests(RunTestCase):
    def test_requester_cannot_approve(self):
        run = self.plan()
        with self.assertRaises(PermissionDenied):
            self.approve(run, self.requester)

    def test_approve_needs_the_permission(self):
        run = self.plan()
        with self.assertRaises(PermissionDenied):
            self.approve(run, self.bystander)
        with self.assertRaises(PermissionDenied):
            actions.reject(run, self.bystander)

    def test_distinct_approver_can_be_switched_off(self):
        settings = {"netbox_alembic": {**PLUGIN_SETTINGS["netbox_alembic"]}}
        settings["netbox_alembic"]["require_distinct_approver"] = False
        run = self.plan()
        with override_settings(PLUGINS_CONFIG=settings):
            run = self.approve(run, self.requester)
        self.assertEqual(run.status, "approved")

    def test_approve_records_the_hash_and_queues_apply(self):
        run = self.approve(self.plan())
        self.assertEqual(run.status, "approved")
        self.assertEqual(run.approved_sha256, run.plan_sha256)
        self.assertEqual(run.decided_by, self.approver)
        self.assertIn(("ApplyRunJob", run.pk), self.enqueued)

    def test_reject(self):
        run = actions.reject(self.plan(), self.approver)
        self.assertEqual(run.status, "rejected")

    def test_old_plans_expire(self):
        run = self.plan()
        Run.objects.filter(pk=run.pk).update(planned_at=timezone.now() - timedelta(days=30))
        with self.assertRaises(ValidationError):
            self.approve(run)
        run.refresh_from_db()
        self.assertEqual(run.status, "expired")

    def test_cannot_approve_twice(self):
        run = self.approve(self.plan())
        with self.assertRaises(ValidationError):
            self.approve(run)


class ApplyTests(RunTestCase):
    def test_apply(self):
        runner = FakeRunner()
        run = self.apply(self.approve(self.plan()), runner)
        self.assertEqual(run.status, "applied")
        self.assertEqual(runner.calls, ["check_stale", "apply"])
        self.assertEqual(run.apply_report["applied"], [{"uid": "u"}])

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

    def test_unapproved_run_does_not_apply(self):
        run = self.plan()
        runner = FakeRunner()
        run = self.apply(run, runner)
        self.assertEqual(run.status, "awaiting_approval")
        self.assertEqual(runner.calls, [])

    def test_failed_apply_resumes_without_stale_check(self):
        run = self.apply(self.approve(self.plan()), FakeRunner(apply_ok=False))
        self.assertEqual(run.status, "apply_failed")
        self.assertIn("boom", run.error)
        with self.captureOnCommitCallbacks(execute=True):
            actions.resume(run, self.approver)
        runner = FakeRunner()
        run = self.apply(run, runner)
        self.assertEqual(run.status, "applied")
        self.assertEqual(runner.calls, ["apply"])

    def test_new_plan_takes_away_the_resume(self):
        failed = self.apply(self.approve(self.plan()), FakeRunner(apply_ok=False))
        self.plan()
        failed.refresh_from_db()
        self.assertEqual(failed.status, "superseded")


@override_settings(PLUGINS_CONFIG=PLUGIN_SETTINGS)
class BackendValidationTests(TestCase):
    def check(self, **fields):
        Backend(name="b", **fields).full_clean()

    def test_valid(self):
        self.check(kind="nautobot", config={"url": "https://n"}, credential="other")
        self.check(kind="netbox", is_self=True)
        self.check(kind="external", external_adapter="store", config={"setup": {"a": 1}})

    def test_invalid(self):
        cases = [
            {"kind": "netbox", "config": {"url": "x", "token": "leak"}},
            {"kind": "external", "external_adapter": "store", "config": {"command": "/bin/sh"}},
            {"kind": "external", "external_adapter": "store", "config": {"env": {"A": "b"}}},
            {"kind": "external", "external_adapter": "nope"},
            {"kind": "netbox", "external_adapter": "store"},
            {"kind": "nautobot", "is_self": True},
            {"kind": "netbox", "is_self": True, "config": {"url": "x"}},
            {"kind": "netbox", "credential": "nope"},
        ]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.check(**fields)


class FlowValidationTests(TestCase):
    def test_paths_stay_inside_the_source(self):
        source = DataSource.objects.create(name="s", type="local", source_url="file:///tmp")
        target = Backend.objects.create(name="t", kind="netbox", config={"url": "https://x"})
        for root, inventory in (("../x", "inv.yaml"), ("", "../inv.yaml"), ("", "")):
            flow = Flow(name="f", data_source=source, target=target, root=root, inventory=inventory)
            with self.subTest(root=root, inventory=inventory), self.assertRaises(ValidationError):
                flow.full_clean()
