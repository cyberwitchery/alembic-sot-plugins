import json
import tempfile
from datetime import timedelta
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
    Workspace,
    hosting,
)
from core.models import DataFile, DataSource
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from users.models import ObjectPermission, User

from netbox_alembic import AlembicConfig, actions, settings
from netbox_alembic.jobs import ApplyRunJob, PlanRunJob
from netbox_alembic.models import Backend, Flow, Run

# an alembic plugins directory with two plugins, as an admin would install them.
PLUGINS_DIR = tempfile.mkdtemp(prefix="netbox-alembic-plugins-")
for _name in ("store", "Lab"):
    Path(PLUGINS_DIR, f"{_name}.yaml").write_text("backend: external\ncommand: /bin/true\n")

PLUGIN_SETTINGS = {
    "netbox_alembic": {
        **AlembicConfig.default_settings,
        "work_root": "/tmp/netbox-alembic-tests",
        "credentials": {
            "netbox": {"token": "TEST_NETBOX_TOKEN"},
            "other": {"token": "TEST_OTHER"},
            "lab": {"env": {"LAB_TOKEN": "TEST_OTHER"}},
        },
        "plugins_dir": PLUGINS_DIR,
    }
}

INVENTORY = "schema:\n  types: {}\n"
CREATE = {"op": "create", "uid": "u", "type_name": "t"}
PLAN = json.dumps({"schema": {"types": {}}, "ops": [CREATE]})
EMPTY_PLAN = json.dumps({"schema": {"types": {}}, "ops": []})
IMPORTED = '{"schema": {"types": {}}, "objects": [], "from": "import"}'
MAPPED = '{"schema": {"types": {}}, "objects": [], "from": "map"}'


def completed(argv=("alembic", "plan"), code=0, stdout="", stderr=""):
    return Completed(list(argv), code, stdout, stderr, 0.1)


class FakeRunner:
    """stands in for alembic_runner.Runner; tests set what each command returns."""

    def __init__(self, plan=PLAN, stale=False, apply_ok=True):
        self.plan_text, self.stale, self.apply_ok = plan, stale, apply_ok
        self.calls = []
        self.planned_from = None
        self.root = Path(tempfile.mkdtemp())

    def check_version(self):
        return "0.9.0"

    def workspace(self, flow):
        return Workspace(self.root, flow.id)

    def _written(self, flow, run, name, text):
        path = Workspace(self.root, flow.id).prepare(run) / name
        path.write_text(text)
        return path

    def import_(self, flow, run, schema_inventory):
        self.calls.append("import")
        path = self._written(flow, run, "imported.json", IMPORTED)
        return InventoryOutcome(completed(("alembic", "import")), inventory=path)

    def map(self, flow, run, inventory, spec):
        self.calls.append("map")
        self.mapped_from = Path(inventory).read_text()
        path = self._written(flow, run, "mapped.json", MAPPED)
        return InventoryOutcome(completed(("alembic", "map")), inventory=path)

    def plan(self, flow, run, inventory):
        self.calls.append("plan")
        self.planned_from = Path(inventory).read_text()
        return PlanOutcome(completed(), plan=Plan.from_bytes(self.plan_text.encode()))

    def apply(self, flow, run, plan, expected):
        self.calls.append("apply")
        if self.stale:
            c = completed(("alembic", "apply"), code=65, stderr="Error: the plan is stale")
            return ApplyOutcome(c, stale=True)
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
        files = {"inv/inventory.yaml": INVENTORY, "inv/map.yaml": "rules: []\n", "other.yaml": "x"}
        for path, content in files.items():
            DataFile.objects.create(
                source=cls.source,
                path=path,
                data=content.encode(),
                size=len(content),
                hash="0" * 64,
                last_updated=timezone.now(),
            )
        cls.target = Backend.objects.create(
            name="netbox", kind="netbox", config={"url": "http://netbox:8080"}, credential="netbox"
        )
        cls.flow = Flow.objects.create(
            name="sites",
            data_source=cls.source,
            root="inv",
            inventory="inventory.yaml",
            target=cls.target,
        )

    def setUp(self):
        patcher = mock.patch.dict("os.environ", {"TEST_NETBOX_TOKEN": "t", "TEST_OTHER": "o"})
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
        # everything under the root, nothing outside it.
        self.assertEqual(run.input, {"inventory.yaml": INVENTORY, "map.yaml": "rules: []\n"})

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
        self.assertIn("TEST_NETBOX_TOKEN", run.error)

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

    def test_editing_the_flow_drops_a_requested_plan(self):
        run = self.request()
        self.flow.comments = "edited"
        self.flow.save()
        runner = FakeRunner()
        with mock.patch("netbox_alembic.jobs.runner", return_value=runner):
            PlanRunJob(job_for(run)).run()
        run.refresh_from_db()
        self.assertEqual(run.status, "superseded")
        self.assertEqual(runner.calls, [])


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
        self.assertEqual(run.decided_by, self.approver)
        self.assertIsNotNone(run.decided_at)
        self.assertIsNotNone(run.finished_at)

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
        self.assertEqual(runner.calls, ["apply"])
        self.assertEqual(run.apply_report["applied"], [{"uid": "u"}])

    def test_stale_plan_writes_nothing(self):
        runner = FakeRunner(stale=True)
        run = self.apply(self.approve(self.plan()), runner)
        self.assertEqual(run.status, "stale")
        self.assertEqual(runner.calls, ["apply"])

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

    def test_failed_apply_resumes(self):
        run = self.apply(self.approve(self.plan()), FakeRunner(apply_ok=False))
        self.assertEqual(run.status, "apply_failed")
        self.assertIn("boom", run.error)
        with self.captureOnCommitCallbacks(execute=True):
            actions.resume(run, self.approver)
        runner = FakeRunner()
        run = self.apply(run, runner)
        self.assertEqual(run.status, "applied")
        self.assertEqual(runner.calls, ["apply"])

    def test_editing_the_flow_takes_away_the_approval(self):
        run = self.approve(self.plan())
        self.flow.comments = "edited"
        self.flow.save()
        runner = FakeRunner()
        run = self.apply(run, runner)
        self.assertEqual(run.status, "superseded")
        self.assertEqual(runner.calls, [])

    def test_new_plan_takes_away_the_resume(self):
        failed = self.apply(self.approve(self.plan()), FakeRunner(apply_ok=False))
        self.plan()
        failed.refresh_from_db()
        self.assertEqual(failed.status, "superseded")

    def applying_elsewhere(self):
        """a run of another flow, applying to the same target."""
        flow = Flow.objects.create(
            name="other", data_source=self.source, inventory="inventory.yaml", target=self.target
        )
        return Run.objects.create(flow=flow, status="applying")

    def test_a_busy_target_keeps_the_approval(self):
        other = self.applying_elsewhere()
        runner = FakeRunner()
        run = self.apply(self.approve(self.plan()), runner)
        self.assertEqual(run.status, "approved")
        self.assertIn("Another run is applying", run.error)
        self.assertEqual(runner.calls, [])
        Run.objects.filter(pk=other.pk).update(status="applied")
        self.enqueued.clear()
        with self.captureOnCommitCallbacks(execute=True):
            actions.resume(run, self.approver)
        self.assertEqual(self.enqueued, [("ApplyRunJob", run.pk)])
        run = self.apply(run, runner)
        self.assertEqual(run.status, "applied")
        self.assertEqual(run.error, "")

    def test_a_busy_target_leaves_a_failed_apply_its_error(self):
        run = self.apply(self.approve(self.plan()), FakeRunner(apply_ok=False))
        error = run.error
        self.applying_elsewhere()
        with self.captureOnCommitCallbacks(execute=True):
            actions.resume(run, self.approver)
        runner = FakeRunner()
        run = self.apply(run, runner)
        self.assertEqual(run.status, "apply_failed")
        self.assertEqual(run.error, error)
        self.assertIn("boom", run.error)
        self.assertEqual(runner.calls, [])

    def test_the_target_is_locked_before_it_is_checked(self):
        run = self.approve(self.plan())
        with CaptureQueriesContext(connection) as queries:
            hosting.start_apply(run)
        sql = [q["sql"] for q in queries.captured_queries]
        locks = [i for i, q in enumerate(sql) if "_backend" in q and "FOR UPDATE" in q]
        checks = [i for i, q in enumerate(sql) if q.startswith("SELECT 1") and "'applying'" in q]
        self.assertTrue(locks and checks and locks[0] < checks[0], sql)


@override_settings(PLUGINS_CONFIG=PLUGIN_SETTINGS)
class SourceTests(RunTestCase):
    """a flow with a source imports its inventory; a map spec reshapes it."""

    def set_flow(self, **fields):
        Flow.objects.filter(pk=self.flow.pk).update(**fields)
        self.flow.refresh_from_db()

    def other_backend(self):
        return Backend.objects.create(
            name="nautobot",
            kind="nautobot",
            config={"url": "http://nautobot:8080"},
            credential="other",
        )

    def test_import_is_what_the_run_plans_from(self):
        self.set_flow(source=self.target, target=self.other_backend())
        runner = FakeRunner()
        run = self.plan(runner)
        self.assertEqual(run.status, "awaiting_approval", run.error)
        self.assertEqual(runner.calls, ["import", "plan"])
        self.assertEqual(run.input, {"inventory.json": IMPORTED})
        self.assertEqual(run.input_entry, "inventory.json")
        self.assertEqual(runner.planned_from, IMPORTED)

    def test_map_reshapes_the_import(self):
        self.set_flow(source=self.target, target=self.other_backend(), map_spec="map.yaml")
        runner = FakeRunner()
        run = self.plan(runner)
        self.assertEqual(runner.calls, ["import", "map", "plan"])
        self.assertEqual(runner.mapped_from, IMPORTED)
        self.assertEqual(run.input, {"inventory.json": MAPPED})

    def test_map_without_a_source_reshapes_the_inventory(self):
        self.set_flow(map_spec="map.yaml")
        runner = FakeRunner()
        self.plan(runner)
        self.assertEqual(runner.calls, ["map", "plan"])
        self.assertEqual(runner.mapped_from, INVENTORY)

    def test_missing_map_spec_fails_the_run(self):
        self.set_flow(map_spec="missing.yaml")
        run = self.plan(FakeRunner())
        self.assertEqual(run.status, "failed")
        self.assertIn("missing.yaml", run.error)


@override_settings(PLUGINS_CONFIG=PLUGIN_SETTINGS)
class BackendValidationTests(TestCase):
    def check(self, **fields):
        Backend(name="b", **fields).full_clean()

    def test_valid(self):
        self.check(kind="nautobot", config={"url": "https://n"}, credential="other")
        self.check(kind="store")
        self.check(kind="lab", credential="lab")

    def test_invalid(self):
        cases = [
            {"kind": "netbox", "config": {"url": "x", "token": "leak"}},
            {"kind": "store", "config": {"setup": {"a": 1}}},
            {"kind": "store", "config": {"command": "/bin/sh"}},
            {"kind": "nope"},
            {"kind": "external"},
            {"kind": "netbox", "credential": "nope"},
        ]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.check(**fields)


@override_settings(PLUGINS_CONFIG=PLUGIN_SETTINGS)
class PluginKindTests(TestCase):
    def test_a_plugin_is_a_kind(self):
        kinds = [kind for kind, _ in settings.kind_choices()]
        self.assertEqual(kinds, ["netbox", "nautobot", "infrahub", "peeringdb", "lab", "store"])

    @mock.patch.dict("os.environ", {"TEST_OTHER": "secret"})
    def test_a_plugin_backend_runs_as_the_plugin(self):
        backend = settings.runner_backend(Backend(name="l", kind="lab", credential="lab"))
        self.assertTrue(backend.plugin)
        self.assertEqual(backend.kind, "lab")
        self.assertEqual(backend.env, {"LAB_TOKEN": "secret"})
        with self.assertRaises(settings.SettingsError):
            settings.runner_backend(Backend(name="s", kind="store", credential="other"))


class FlowValidationTests(TestCase):
    def test_paths_stay_inside_the_source(self):
        source = DataSource.objects.create(name="s", type="local", source_url="file:///tmp")
        target = Backend.objects.create(name="t", kind="netbox", config={"url": "https://x"})
        for root, inventory in (("../x", "inv.yaml"), ("", "../inv.yaml"), ("", "")):
            flow = Flow(name="f", data_source=source, target=target, root=root, inventory=inventory)
            with self.subTest(root=root, inventory=inventory), self.assertRaises(ValidationError):
                flow.full_clean()

    def test_a_flow_that_may_delete_adopts(self):
        source = DataSource.objects.create(name="s", type="local", source_url="file:///tmp")
        target = Backend.objects.create(name="t", kind="netbox", config={"url": "https://x"})
        flow = Flow(
            name="f",
            data_source=source,
            target=target,
            inventory="inv.yaml",
            allow_delete=True,
            no_adopt=True,
        )
        with self.assertRaises(ValidationError) as caught:
            flow.full_clean()
        self.assertIn("no_adopt", caught.exception.message_dict)
        flow.no_adopt = False
        flow.full_clean()
