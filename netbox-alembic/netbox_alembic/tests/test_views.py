"""the ui and the rest api: who may do what, and what each page shows."""

import json
from unittest import mock

from alembic_runner import Plan, RunStatus
from core.models import DataSource
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from users.models import ObjectPermission, User

from netbox_alembic import AlembicConfig
from netbox_alembic.jobs import ApplyRunJob, PlanRunJob
from netbox_alembic.models import Backend, Flow, Run

SETTINGS = {"netbox_alembic": {**AlembicConfig.default_settings}}
PLAN = json.dumps(
    {"schema": {"types": {}}, "ops": [{"op": "create", "uid": "u", "type_name": "t"}]}
)


def grant(name, models, actions, *users):
    permission = ObjectPermission.objects.create(name=name, actions=actions)
    permission.object_types.add(*(ContentType.objects.get_for_model(m) for m in models))
    permission.users.add(*users)


@override_settings(PLUGINS_CONFIG=SETTINGS, LOGIN_REQUIRED=True)
class ViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create_user(username="requester")
        cls.approver = User.objects.create_user(username="approver")
        cls.viewer = User.objects.create_user(username="viewer")
        everyone = (cls.requester, cls.approver, cls.viewer)
        grant("view", (Backend, Flow, Run), ["view"], *everyone)
        grant("start", (Run,), ["add"], cls.requester)
        grant("approve", (Run,), ["approve"], cls.requester, cls.approver)
        source = DataSource.objects.create(name="s", type="local", source_url="file:///tmp")
        cls.target = Backend.objects.create(name="t", kind="netbox", config={"url": "http://x"})
        cls.flow = Flow.objects.create(
            name="f", data_source=source, inventory="inventory.yaml", target=cls.target
        )

    def setUp(self):
        self.enqueued = []
        for job in (PlanRunJob, ApplyRunJob):
            p = mock.patch.object(
                job, "enqueue", lambda *a, _j=job, **k: self.enqueued.append(_j.__name__)
            )
            p.start()
            self.addCleanup(p.stop)

    def waiting_run(self, flow=None):
        return Run.objects.create(
            flow=flow or self.flow,
            status=RunStatus.AWAITING_APPROVAL.value,
            requested_by=self.requester,
            plan=PLAN,
            plan_sha256=Plan.from_bytes(PLAN.encode()).sha256,
            planned_at=timezone.now(),
            summary={"create": 1, "update": 0, "delete": 0, "schema_preview": {}},
        )

    def client_for(self, user):
        client = Client()
        client.force_login(user)
        return client

    def api_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client


class PageTests(ViewTestCase):
    def test_run_page_renders_in_every_status(self):
        client = self.client_for(self.viewer)
        for status in RunStatus:
            flow = Flow.objects.create(
                name=f"f-{status.value}",
                data_source=self.flow.data_source,
                inventory="inventory.yaml",
                target=self.target,
            )
            run = self.waiting_run(flow)
            Run.objects.filter(pk=run.pk).update(status=status.value)
            with self.subTest(status=status.value):
                self.assertEqual(client.get(run.get_absolute_url()).status_code, 200)

    def test_decision_is_offered_only_to_an_approver(self):
        run = self.waiting_run()
        page = self.client_for(self.approver).get(run.get_absolute_url()).content.decode()
        self.assertIn("Approve and apply", page)
        page = self.client_for(self.requester).get(run.get_absolute_url()).content.decode()
        self.assertNotIn("Approve and apply", page)
        self.assertIn("Someone other than the requester approves this run.", page)

    def test_plan_download_is_the_stored_bytes(self):
        run = self.waiting_run()
        url = reverse("plugins:netbox_alembic:run_plan_json", args=[run.pk])
        self.assertEqual(self.client_for(self.viewer).get(url).content.decode(), PLAN)


class UiActionTests(ViewTestCase):
    def plan_url(self):
        return reverse("plugins:netbox_alembic:flow_plan", args=[self.flow.pk])

    def test_starting_a_run_needs_the_add_permission(self):
        self.client_for(self.viewer).post(self.plan_url(), {"kind": "plan"})
        self.assertFalse(Run.objects.exists())
        self.client_for(self.requester).post(self.plan_url(), {"kind": "plan"})
        self.assertEqual(Run.objects.get().status, "pending")

    def test_requester_approval_is_refused(self):
        run = self.waiting_run()
        url = reverse("plugins:netbox_alembic:run_approve", args=[run.pk])
        self.client_for(self.requester).post(url)
        run.refresh_from_db()
        self.assertEqual(run.status, "awaiting_approval")
        self.assertEqual(self.enqueued, [])

    def test_approver_approves(self):
        run = self.waiting_run()
        url = reverse("plugins:netbox_alembic:run_approve", args=[run.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.client_for(self.approver).post(url)
        run.refresh_from_db()
        self.assertEqual(run.status, "approved")
        self.assertEqual(self.enqueued, ["ApplyRunJob"])


class ApiTests(ViewTestCase):
    def test_plan(self):
        url = f"/api/plugins/alembic/flows/{self.flow.pk}/plan/"
        self.assertEqual(self.api_for(self.viewer).post(url, {}).status_code, 403)
        self.assertEqual(self.api_for(self.requester).post(url, {"kind": "nope"}).status_code, 400)
        response = self.api_for(self.requester).post(url, {}, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"]["value"], "pending")

    def test_a_second_run_in_progress_is_a_conflict(self):
        url = f"/api/plugins/alembic/flows/{self.flow.pk}/plan/"
        self.waiting_run()
        Run.objects.update(status="planning")
        self.assertEqual(self.api_for(self.requester).post(url, {}).status_code, 409)

    def test_approve_and_reject(self):
        run = self.waiting_run()
        base = f"/api/plugins/alembic/runs/{run.pk}"
        self.assertEqual(self.api_for(self.requester).post(f"{base}/approve/").status_code, 403)
        self.assertEqual(self.api_for(self.viewer).post(f"{base}/reject/").status_code, 403)
        self.assertEqual(self.api_for(self.approver).post(f"{base}/approve/").status_code, 202)
        self.assertEqual(self.api_for(self.approver).post(f"{base}/reject/").status_code, 409)

    def test_plan_file(self):
        run = self.waiting_run()
        response = self.api_for(self.viewer).get(f"/api/plugins/alembic/runs/{run.pk}/plan/")
        self.assertEqual(response.content.decode(), PLAN)

    def test_backend_config_refuses_secrets(self):
        response = self.api_for(User.objects.create_user("admin", is_superuser=True)).post(
            "/api/plugins/alembic/backends/",
            {"name": "b", "kind": "netbox", "config": {"url": "x", "token": "leak"}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("token", json.dumps(response.json()))
