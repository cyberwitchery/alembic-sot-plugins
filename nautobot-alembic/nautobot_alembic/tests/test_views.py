"""the ui and the rest api: who may do what, and what each page shows."""

import json
from unittest import mock

from alembic_runner import Plan, RunStatus
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from nautobot.extras.models import GitRepository
from nautobot.users.models import ObjectPermission
from rest_framework.test import APIClient

from nautobot_alembic import actions
from nautobot_alembic.models import Backend, Flow, Run

User = get_user_model()
PLAN = json.dumps(
    {"schema": {"types": {}}, "ops": [{"op": "create", "uid": "u", "type_name": "t"}]}
)


def grant(name, models, verbs, *users):
    permission = ObjectPermission.objects.create(name=name, actions=verbs)
    permission.object_types.add(*(ContentType.objects.get_for_model(m) for m in models))
    permission.users.add(*users)


@override_settings(PLUGINS_CONFIG={"nautobot_alembic": {}})
class ViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create_user(username="requester")
        cls.approver = User.objects.create_user(username="approver")
        cls.viewer = User.objects.create_user(username="viewer")
        grant("view", (Backend, Flow, Run), ["view"], cls.requester, cls.approver, cls.viewer)
        grant("start", (Run,), ["add"], cls.requester)
        grant("approve", (Run,), ["approve"], cls.requester, cls.approver)
        repository = GitRepository.objects.create(
            name="r", slug="r", remote_url="file:///tmp/none", branch="main"
        )
        cls.target = Backend.objects.create(name="t", kind="nautobot", config={"url": "http://x"})
        cls.flow = Flow.objects.create(
            name="f", git_repository=repository, inventory="inventory.yaml", target=cls.target
        )

    def setUp(self):
        self.enqueued = []
        patcher = mock.patch.object(
            actions, "enqueue", lambda name, run, user: self.enqueued.append(name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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
                git_repository=self.flow.git_repository,
                inventory="inventory.yaml",
                target=self.target,
            )
            run = self.waiting_run(flow)
            Run.objects.filter(pk=run.pk).update(status=status.value)
            with self.subTest(status=status.value):
                self.assertEqual(client.get(run.get_absolute_url()).status_code, 200)

    def test_list_pages_offer_only_buttons_with_views(self):
        for name in ("backend_list", "flow_list", "run_list"):
            with self.subTest(page=name):
                response = self.client_for(self.viewer).get(
                    reverse(f"plugins:nautobot_alembic:{name}")
                )
                errors = [str(m) for m in get_messages(response.wsgi_request)]
                self.assertEqual(errors, [])

    def test_decision_is_offered_only_to_an_approver(self):
        run = self.waiting_run()
        page = self.client_for(self.approver).get(run.get_absolute_url()).content.decode()
        self.assertIn("Approve and apply", page)
        page = self.client_for(self.requester).get(run.get_absolute_url()).content.decode()
        self.assertNotIn("Approve and apply", page)
        self.assertIn("Someone other than the requester approves this run.", page)

    def test_plan_download_is_the_stored_bytes(self):
        run = self.waiting_run()
        url = reverse("plugins:nautobot_alembic:run_plan_json", args=[run.pk])
        self.assertEqual(self.client_for(self.viewer).get(url).content.decode(), PLAN)


class UiActionTests(ViewTestCase):
    def plan_url(self):
        return reverse("plugins:nautobot_alembic:flow_plan", args=[self.flow.pk])

    def test_starting_a_run_needs_the_add_permission(self):
        self.client_for(self.viewer).post(self.plan_url(), {"kind": "plan"})
        self.assertFalse(Run.objects.exists())
        self.client_for(self.requester).post(self.plan_url(), {"kind": "plan"})
        self.assertEqual(Run.objects.get().status, "pending")

    def test_requester_approval_is_refused(self):
        run = self.waiting_run()
        self.client_for(self.requester).post(
            reverse("plugins:nautobot_alembic:run_approve", args=[run.pk])
        )
        run.refresh_from_db()
        self.assertEqual(run.status, "awaiting_approval")
        self.assertEqual(self.enqueued, [])

    def test_approver_approves(self):
        run = self.waiting_run()
        with self.captureOnCommitCallbacks(execute=True):
            self.client_for(self.approver).post(
                reverse("plugins:nautobot_alembic:run_approve", args=[run.pk])
            )
        run.refresh_from_db()
        self.assertEqual(run.status, "approved")
        self.assertEqual(self.enqueued, ["ApplyRun"])


class ApiTests(ViewTestCase):
    def test_plan(self):
        url = f"/api/plugins/alembic/flows/{self.flow.pk}/plan/"
        self.assertEqual(self.api_for(self.viewer).post(url, {}, format="json").status_code, 403)
        self.assertEqual(
            self.api_for(self.requester).post(url, {"kind": "nope"}, format="json").status_code, 400
        )
        response = self.api_for(self.requester).post(url, {}, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "pending")

    def test_a_second_run_in_progress_is_a_conflict(self):
        url = f"/api/plugins/alembic/flows/{self.flow.pk}/plan/"
        self.waiting_run()
        Run.objects.update(status="planning")
        self.assertEqual(self.api_for(self.requester).post(url, {}, format="json").status_code, 409)

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
        admin = User.objects.create_user(username="admin-user", is_superuser=True)
        response = self.api_for(admin).post(
            "/api/plugins/alembic/backends/",
            {"name": "b", "kind": "netbox", "config": {"url": "x", "token": "leak"}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("token", json.dumps(response.json()))
